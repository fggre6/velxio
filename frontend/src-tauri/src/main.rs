#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Velxio desktop shell.
//!
//! Responsibilities:
//!   * pick a free loopback port and launch the PyInstaller backend with --port
//!   * inject window.__VELXIO_BACKEND__ / window.__VELXIO_API_BASE__ before the
//!     SPA loads (never file://, always http://127.0.0.1:<port>/)
//!   * single instance, with an advisory lock at %APPDATA%\Velxio\.lock
//!   * kill the backend when the window closes (explicit kill + Windows Job
//!     Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE as a crash safety net)

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct BackendProcess {
    child: Child,
    lock_path: PathBuf,
    #[cfg(windows)]
    job: usize,
}

struct AppState {
    backend: Mutex<Option<BackendProcess>>,
}

fn roaming_velxio() -> PathBuf {
    std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Velxio")
}

fn local_velxio() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Velxio")
}

fn pick_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

fn acquire_lock(data_dir: &Path) -> std::io::Result<(File, PathBuf)> {
    fs::create_dir_all(data_dir)?;
    let path = data_dir.join(".lock");
    let mut file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&path)?;
    writeln!(file, "{}", std::process::id())?;
    Ok((file, path))
}

fn wait_for_backend(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    false
}

#[cfg(windows)]
fn create_kill_on_close_job() -> Option<usize> {
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return None;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 {
            return None;
        }
        Some(job as usize)
    }
}

#[cfg(windows)]
fn assign_to_job(job: usize, child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;

    unsafe {
        let _ = AssignProcessToJobObject(
            job as *mut core::ffi::c_void,
            child.as_raw_handle() as *mut core::ffi::c_void,
        );
    }
}

fn spawn_backend(
    exe: &Path,
    resources: &Path,
    data_dir: &Path,
    log_dir: &Path,
    port: u16,
) -> std::io::Result<BackendProcess> {
    let (lock_file, lock_path) = acquire_lock(data_dir)?;
    drop(lock_file); // advisory only; tauri-plugin-single-instance enforces uniqueness

    let cache_dir = local_velxio().join("cache");
    fs::create_dir_all(&cache_dir)?;
    fs::create_dir_all(log_dir)?;

    let backend_dir = exe.parent().unwrap_or(resources).to_path_buf();
    let toolchain_root = if resources.join("resources").is_dir() {
        resources.join("resources")
    } else {
        resources.to_path_buf()
    };
    let wasi_sdk = toolchain_root.join("wasi-sdk");
    let sdcc_root = toolchain_root.join("sdcc");

    let log_path = log_dir.join("backend.stdout.log");
    let log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;

    let mut cmd = Command::new(exe);
    cmd.arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .current_dir(&backend_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::from(log_file.try_clone()?))
        .stderr(Stdio::from(log_file))
        .env("VELXIO_DATA_DIR", data_dir)
        .env("VELXIO_LOG_DIR", log_dir)
        .env("VELXIO_CACHE_DIR", &cache_dir)
        .env("VELXIO_BUILD_ROOT", data_dir.join("build"))
        .env("VELXIO_ARTIFACT_CACHE", data_dir.join("build").join("artifacts"))
        .env("VELXIO_LIBCACHE_DIR", data_dir.join("libcache"))
        .env("VELXIO_NEWS", "off")
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");

    if wasi_sdk.join("bin").join("clang.exe").is_file() {
        cmd.env("WASI_SDK_PATH", &wasi_sdk);
        cmd.env("WASI_SDK", &wasi_sdk);
    }
    let sdcc = sdcc_root.join("bin").join("sdcc.exe");
    if sdcc.is_file() {
        cmd.env("SDCC", &sdcc);
    }

    // Make the bundled toolchain visible to any child process (clang, sdcc,
    // and the DLLs shipped next to the backend).
    let mut search: Vec<PathBuf> = vec![
        backend_dir.clone(),
        wasi_sdk.join("bin"),
        sdcc_root.join("bin"),
    ];
    if let Some(existing) = std::env::var_os("PATH") {
        search.extend(std::env::split_paths(&existing));
    }
    if let Ok(joined) = std::env::join_paths(search) {
        cmd.env("PATH", joined);
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = cmd.spawn()?;

    #[cfg(windows)]
    let job = {
        let job = create_kill_on_close_job().unwrap_or(0);
        if job != 0 {
            assign_to_job(job, &child);
        }
        job
    };

    Ok(BackendProcess {
        child,
        lock_path,
        #[cfg(windows)]
        job,
    })
}

fn shutdown(state: &AppState) {
    let mut guard = match state.backend.lock() {
        Ok(g) => g,
        Err(poisoned) => poisoned.into_inner(),
    };
    if let Some(mut backend) = guard.take() {
        let _ = backend.child.kill();
        let _ = backend.child.wait();
        let _ = fs::remove_file(&backend.lock_path);
        #[cfg(windows)]
        {
            let _ = backend.job; // handle closes with the process
        }
    }
}

fn initialize_script(port: u16) -> String {
    format!(
        "window.__VELXIO_BACKEND__ = 'http://127.0.0.1:{port}';\n\
         window.__VELXIO_API_BASE__ = 'http://127.0.0.1:{port}/api';\n"
    )
}

fn open_main_window(app: &AppHandle, port: u16) {
    let url = format!("http://127.0.0.1:{port}/");
    let parsed = match url.parse() {
        Ok(u) => u,
        Err(err) => {
            eprintln!("invalid backend URL {url}: {err}");
            return;
        }
    };
    let script = initialize_script(port);
    let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
        .title("Velxio")
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 640.0)
        .initialization_script(&script);
    #[cfg(debug_assertions)]
    let builder = builder.open_devtools();

    if let Err(err) = builder.build() {
        eprintln!("failed to create main window: {err}");
    }
}

#[tauri::command]
fn write_debug_log(message: String) -> Result<(), String> {
    let dir = local_velxio().join("logs");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("desktop-debug.log"))
        .map_err(|e| e.to_string())?;
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    writeln!(file, "{ts} {message}").map_err(|e| e.to_string())
}

fn main() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![write_debug_log])
        .setup(|app| {
            let resources = app.path().resource_dir()?;
            let toolchain_root = if resources.join("resources").is_dir() {
                resources.join("resources")
            } else {
                resources.clone()
            };
            let backend_exe = toolchain_root.join("backend").join("velxio-backend.exe");
            if !backend_exe.is_file() {
                return Err(format!(
                    "bundled backend not found at {}. The package was built without \
                     PyInstaller output in src-tauri/resources/backend.",
                    backend_exe.display()
                )
                .into());
            }

            let data_dir = roaming_velxio();
            let log_dir = local_velxio().join("logs");
            let port = pick_port()?;

            let backend =
                spawn_backend(&backend_exe, &resources, &data_dir, &log_dir, port)?;
            app.manage(AppState {
                backend: Mutex::new(Some(backend)),
            });

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if !wait_for_backend(port, Duration::from_secs(60)) {
                    let dir = local_velxio().join("logs");
                    let _ = fs::create_dir_all(&dir);
                    if let Ok(mut f) = OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(dir.join("desktop-debug.log"))
                    {
                        let _ = writeln!(f, "backend did not open port {port} in 60s");
                    }
                }
                let h = handle.clone();
                let _ = handle.run_on_main_thread(move || open_main_window(&h, port));
            });

            Ok(())
        });

    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building Velxio");

    app.run(|handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            if let Some(state) = handle.try_state::<AppState>() {
                shutdown(state.inner());
            }
        }
    });
}
