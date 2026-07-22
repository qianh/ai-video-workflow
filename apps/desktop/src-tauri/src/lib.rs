pub mod protocol;
pub mod supervisor;

use std::ffi::OsString;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;
use tauri::{Emitter, Manager, State};

use supervisor::{SidecarLaunch, SidecarStatus, SidecarSupervisor};

struct AppState {
    sidecar: SidecarSupervisor,
}

#[tauri::command]
async fn sidecar_request(
    method: String,
    params: Value,
    request_id: Option<String>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    match request_id {
        Some(request_id) => state
            .sidecar
            .request_with_id(&request_id, &method, params)
            .await,
        None => state.sidecar.request(&method, params).await,
    }
    .map_err(|error| error.to_string())
}

#[tauri::command]
async fn sidecar_cancel(
    request_id: String,
    state: State<'_, AppState>,
) -> Result<bool, String> {
    state
        .sidecar
        .cancel(&request_id)
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn sidecar_status(state: State<'_, AppState>) -> Result<SidecarStatus, String> {
    Ok(state.sidecar.status().await)
}

#[tauri::command]
async fn sidecar_restart(state: State<'_, AppState>) -> Result<SidecarStatus, String> {
    state
        .sidecar
        .shutdown()
        .await
        .map_err(|error| error.to_string())?;
    state
        .sidecar
        .request("system.ping", serde_json::json!({}))
        .await
        .map_err(|error| error.to_string())?;
    Ok(state.sidecar.status().await)
}

fn development_launch() -> SidecarLaunch {
    if let Some(binary) = std::env::var_os("WORKFLOW_SIDECAR_BINARY") {
        let path = PathBuf::from(&binary);
        let cwd = path
            .parent()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        return SidecarLaunch::binary(binary, cwd, cfg!(debug_assertions));
    }

    if !cfg!(debug_assertions) {
        let binary = std::env::current_exe()
            .expect("packaged application path should be available")
            .with_file_name("workflow-sidecar");
        let cwd = binary
            .parent()
            .expect("packaged sidecar should have a parent directory")
            .to_path_buf();
        return SidecarLaunch::binary(binary.into_os_string(), cwd, false);
    }

    let sidecar_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../services/sidecar");
    let python = std::env::var_os("WORKFLOW_SIDECAR_PYTHON")
        .unwrap_or_else(|| OsString::from("python3"));
    SidecarLaunch::python_module(python, sidecar_root, cfg!(debug_assertions))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let sidecar = SidecarSupervisor::new(development_launch(), Duration::from_secs(30));
            let mut events = sidecar.subscribe();
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Ok(event) = events.recv().await {
                    let _ = app_handle.emit("sidecar-event", event);
                }
            });
            app.manage(AppState { sidecar });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_request,
            sidecar_cancel,
            sidecar_status,
            sidecar_restart
        ])
        .run(tauri::generate_context!())
        .expect("error while running AI Video Workflow");
}
