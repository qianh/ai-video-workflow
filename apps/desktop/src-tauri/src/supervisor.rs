use std::collections::HashMap;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{broadcast, oneshot, Mutex};
use tokio::task::JoinHandle;
use tokio::time::timeout;
use uuid::Uuid;

use crate::protocol::{decode_inbound, InboundMessage, MAX_MESSAGE_BYTES, PROTOCOL_VERSION};

type PendingResult = Result<Value, SidecarError>;
type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<PendingResult>>>>;

#[derive(Debug, Clone)]
pub struct SidecarLaunch {
    pub program: OsString,
    pub args: Vec<OsString>,
    pub cwd: PathBuf,
    pub env: Vec<(OsString, OsString)>,
}

impl SidecarLaunch {
    pub fn python_module(program: OsString, sidecar_root: PathBuf, test_methods: bool) -> Self {
        let source_path = sidecar_root.join("src");
        let mut env = vec![(OsString::from("PYTHONPATH"), source_path.into_os_string())];
        if test_methods {
            env.push((
                OsString::from("WORKFLOW_SIDECAR_ENABLE_TEST_METHODS"),
                OsString::from("1"),
            ));
        }
        Self {
            program,
            args: vec![OsString::from("-m"), OsString::from("workflow_sidecar")],
            cwd: sidecar_root,
            env,
        }
    }

    pub fn binary(program: OsString, cwd: PathBuf, test_methods: bool) -> Self {
        let mut env = Vec::new();
        if test_methods {
            env.push((
                OsString::from("WORKFLOW_SIDECAR_ENABLE_TEST_METHODS"),
                OsString::from("1"),
            ));
        }
        Self {
            program,
            args: Vec::new(),
            cwd,
            env,
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SidecarEvent {
    pub event: String,
    pub data: Value,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SidecarStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub restart_count: u64,
}

#[derive(Debug, Error)]
pub enum SidecarError {
    #[error("sidecar I/O failed: {0}")]
    Io(String),
    #[error("sidecar protocol failed: {0}")]
    Protocol(String),
    #[error("request timed out: {0}")]
    Timeout(String),
    #[error("sidecar exited before responding")]
    Exited,
    #[error("duplicate request id: {0}")]
    DuplicateRequestId(String),
    #[error("sidecar returned {code}: {message}")]
    Remote {
        code: String,
        message: String,
        diagnostic_id: Option<String>,
    },
}

#[derive(Clone)]
pub struct SidecarSupervisor {
    shared: Arc<Shared>,
}

struct Shared {
    launch: SidecarLaunch,
    request_timeout: Duration,
    inner: Mutex<SupervisorState>,
    events: broadcast::Sender<SidecarEvent>,
}

#[derive(Default)]
struct SupervisorState {
    process: Option<RunningSidecar>,
    start_count: u64,
}

struct RunningSidecar {
    child: Child,
    stdin: ChildStdin,
    pending: Pending,
    alive: Arc<AtomicBool>,
    pid: Option<u32>,
    stdout_task: JoinHandle<()>,
    stderr_task: JoinHandle<()>,
}

impl SidecarSupervisor {
    pub fn new(launch: SidecarLaunch, request_timeout: Duration) -> Self {
        let (events, _) = broadcast::channel(256);
        Self {
            shared: Arc::new(Shared {
                launch,
                request_timeout,
                inner: Mutex::new(SupervisorState::default()),
                events,
            }),
        }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<SidecarEvent> {
        self.shared.events.subscribe()
    }

    pub async fn request(&self, method: &str, params: Value) -> PendingResult {
        let request_id = format!("req_{}", Uuid::now_v7().simple());
        self.request_with_id(&request_id, method, params).await
    }

    pub async fn request_with_id(
        &self,
        request_id: &str,
        method: &str,
        params: Value,
    ) -> PendingResult {
        let (sender, receiver) = oneshot::channel();
        let pending;
        {
            let mut state = self.shared.inner.lock().await;
            self.ensure_running(&mut state).await?;
            let process = state.process.as_mut().expect("sidecar was just started");
            pending = process.pending.clone();
            {
                let mut requests = pending.lock().await;
                if requests.contains_key(request_id) {
                    return Err(SidecarError::DuplicateRequestId(request_id.to_owned()));
                }
                requests.insert(request_id.to_owned(), sender);
            }

            let payload = serde_json::to_vec(&json!({
                "v": PROTOCOL_VERSION,
                "type": "request",
                "id": request_id,
                "method": method,
                "params": params,
            }))
            .map_err(|error| SidecarError::Protocol(error.to_string()))?;

            if let Err(error) = process.stdin.write_all(&payload).await {
                pending.lock().await.remove(request_id);
                process.alive.store(false, Ordering::Release);
                return Err(SidecarError::Io(error.to_string()));
            }
            if let Err(error) = process.stdin.write_all(b"\n").await {
                pending.lock().await.remove(request_id);
                process.alive.store(false, Ordering::Release);
                return Err(SidecarError::Io(error.to_string()));
            }
            if let Err(error) = process.stdin.flush().await {
                pending.lock().await.remove(request_id);
                process.alive.store(false, Ordering::Release);
                return Err(SidecarError::Io(error.to_string()));
            }
        }

        match timeout(self.shared.request_timeout, receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(SidecarError::Exited),
            Err(_) => {
                pending.lock().await.remove(request_id);
                Err(SidecarError::Timeout(request_id.to_owned()))
            }
        }
    }

    pub async fn cancel(&self, request_id: &str) -> Result<bool, SidecarError> {
        let result = self
            .request("request.cancel", json!({"request_id": request_id}))
            .await?;
        Ok(result
            .get("cancelled")
            .and_then(Value::as_bool)
            .unwrap_or(false))
    }

    pub async fn status(&self) -> SidecarStatus {
        let mut state = self.shared.inner.lock().await;
        let (running, pid) = match state.process.as_mut() {
            Some(process) if process.alive.load(Ordering::Acquire) => {
                let running = process.child.try_wait().ok().flatten().is_none();
                if !running {
                    process.alive.store(false, Ordering::Release);
                }
                (running, running.then_some(process.pid).flatten())
            }
            _ => (false, None),
        };
        SidecarStatus {
            running,
            pid,
            restart_count: state.start_count.saturating_sub(1),
        }
    }

    pub async fn shutdown(&self) -> Result<(), SidecarError> {
        let process = self.shared.inner.lock().await.process.take();
        if let Some(mut process) = process {
            process.alive.store(false, Ordering::Release);
            let _ = process.child.kill().await;
            let _ = process.child.wait().await;
            process.stdout_task.abort();
            process.stderr_task.abort();
            fail_pending(&process.pending, SidecarError::Exited).await;
        }
        Ok(())
    }

    async fn ensure_running(&self, state: &mut SupervisorState) -> Result<(), SidecarError> {
        if let Some(process) = state.process.as_mut() {
            let alive = process.alive.load(Ordering::Acquire)
                && process
                    .child
                    .try_wait()
                    .map_err(|error| SidecarError::Io(error.to_string()))?
                    .is_none();
            if alive {
                return Ok(());
            }
        }

        if let Some(mut old) = state.process.take() {
            old.alive.store(false, Ordering::Release);
            let _ = old.child.kill().await;
            let _ = old.child.wait().await;
            old.stdout_task.abort();
            old.stderr_task.abort();
        }

        state.process = Some(self.spawn().await?);
        state.start_count += 1;
        Ok(())
    }

    async fn spawn(&self) -> Result<RunningSidecar, SidecarError> {
        let launch = &self.shared.launch;
        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .current_dir(&launch.cwd)
            .envs(launch.env.iter().cloned())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        let mut child = command
            .spawn()
            .map_err(|error| SidecarError::Io(error.to_string()))?;
        let pid = child.id();
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| SidecarError::Io("sidecar stdin was not piped".into()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| SidecarError::Io("sidecar stdout was not piped".into()))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| SidecarError::Io("sidecar stderr was not piped".into()))?;

        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let alive = Arc::new(AtomicBool::new(true));
        let stdout_task = tokio::spawn(read_stdout(
            stdout,
            pending.clone(),
            self.shared.events.clone(),
            alive.clone(),
        ));
        let stderr_task = tokio::spawn(drain_stderr(stderr));

        Ok(RunningSidecar {
            child,
            stdin,
            pending,
            alive,
            pid,
            stdout_task,
            stderr_task,
        })
    }
}

async fn read_stdout(
    stdout: tokio::process::ChildStdout,
    pending: Pending,
    events: broadcast::Sender<SidecarEvent>,
    alive: Arc<AtomicBool>,
) {
    let mut reader = BufReader::new(stdout);
    let mut line = Vec::new();
    loop {
        line.clear();
        match reader.read_until(b'\n', &mut line).await {
            Ok(0) | Err(_) => break,
            Ok(_) if line.len() > MAX_MESSAGE_BYTES + 2 => break,
            Ok(_) => {}
        }
        while matches!(line.last(), Some(b'\n' | b'\r')) {
            line.pop();
        }
        match decode_inbound(&line) {
            Ok(InboundMessage::Response { id, result, error }) => {
                if let Some(id) = id {
                    if let Some(sender) = pending.lock().await.remove(&id) {
                        let result = match error {
                            Some(error) => Err(SidecarError::Remote {
                                code: error.code,
                                message: error.message,
                                diagnostic_id: error.diagnostic_id,
                            }),
                            None => Ok(result.unwrap_or(Value::Null)),
                        };
                        let _ = sender.send(result);
                    }
                }
            }
            Ok(InboundMessage::Event { event, data }) => {
                let _ = events.send(SidecarEvent { event, data });
            }
            Err(_) => break,
        }
    }

    alive.store(false, Ordering::Release);
    fail_pending(&pending, SidecarError::Exited).await;
}

async fn drain_stderr(stderr: tokio::process::ChildStderr) {
    let mut reader = BufReader::new(stderr);
    let mut line = Vec::new();
    loop {
        line.clear();
        match reader.read_until(b'\n', &mut line).await {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                if line.len() > 16 * 1024 {
                    line.truncate(16 * 1024);
                }
                eprint!("[workflow-sidecar] {}", String::from_utf8_lossy(&line));
            }
        }
    }
}

async fn fail_pending(pending: &Pending, error: SidecarError) {
    let requests = std::mem::take(&mut *pending.lock().await);
    let message = error.to_string();
    for (_, sender) in requests {
        let _ = sender.send(Err(SidecarError::Io(message.clone())));
    }
}
