use std::path::PathBuf;
use std::time::Duration;

use ai_video_workflow_desktop::supervisor::{SidecarError, SidecarLaunch, SidecarSupervisor};
use serde_json::json;
use tokio::time::timeout;

fn launch() -> SidecarLaunch {
    if let Some(binary) = std::env::var_os("WORKFLOW_SIDECAR_TEST_BINARY") {
        let path = PathBuf::from(&binary);
        return SidecarLaunch::binary(
            binary,
            path.parent()
                .expect("packaged sidecar should have a parent")
                .to_path_buf(),
            true,
        );
    }
    let sidecar_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../services/sidecar")
        .canonicalize()
        .expect("sidecar source should exist");
    SidecarLaunch::python_module(
        std::env::var_os("PYTHON").unwrap_or_else(|| "python3".into()),
        sidecar_root,
        true,
    )
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn exchanges_1000_messages_without_protocol_pollution() {
    let supervisor = SidecarSupervisor::new(launch(), Duration::from_secs(5));

    for index in 0..1000 {
        let result = supervisor
            .request("system.ping", json!({"echo": index}))
            .await
            .expect("ping should succeed");
        assert_eq!(result["echo"], index);
    }

    let status = supervisor.status().await;
    assert!(status.running);
    assert!(status.pid.is_some());
    supervisor.shutdown().await.expect("sidecar should stop");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn forwards_progress_and_cancels_inflight_request() {
    let supervisor = SidecarSupervisor::new(launch(), Duration::from_secs(5));
    let mut events = supervisor.subscribe();
    let worker = supervisor.clone();
    let request = tokio::spawn(async move {
        worker
            .request_with_id(
                "count_cancel",
                "diagnostics.count",
                json!({"steps": 100, "delay_ms": 10}),
            )
            .await
    });

    let progress = timeout(Duration::from_secs(2), events.recv())
        .await
        .expect("progress should arrive")
        .expect("event channel should stay open");
    assert_eq!(progress.event, "request.progress");
    assert_eq!(progress.data["request_id"], "count_cancel");

    assert!(supervisor
        .cancel("count_cancel")
        .await
        .expect("cancel request should succeed"));
    let error = request
        .await
        .expect("request task should join")
        .expect_err("cancelled work should return an error");
    assert!(matches!(error, SidecarError::Remote { ref code, .. } if code == "CANCELLED"));
    supervisor.shutdown().await.expect("sidecar should stop");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn detects_crash_and_restarts_on_next_request() {
    let supervisor = SidecarSupervisor::new(launch(), Duration::from_secs(5));
    supervisor
        .request("system.ping", json!({}))
        .await
        .expect("initial ping should start sidecar");
    let before = supervisor.status().await;

    let crash = supervisor
        .request("diagnostics.crash", json!({"exit_code": 73}))
        .await;
    assert!(crash.is_err());

    let recovered = supervisor
        .request("system.ping", json!({"echo": "recovered"}))
        .await
        .expect("next request should restart sidecar");
    let after = supervisor.status().await;

    assert_eq!(recovered["echo"], "recovered");
    assert_ne!(before.pid, after.pid);
    assert!(after.restart_count >= 1);
    supervisor.shutdown().await.expect("sidecar should stop");
}
