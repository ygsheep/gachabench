#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;

struct Backend {
    child: Option<Child>,
    /// Windows Job Object 句柄。**故意不关闭** —— 见 put_child_in_kill_job()。
    job: Option<isize>,
    log: PathBuf,
}

/// 后端可执行文件名（PyInstaller onedir 的产物）
const BACKEND_EXE: &str = "GachaBench-backend.exe";

/// TCP 探活总时长：后端 lifespan 里有一次 reconcile，最坏 40s，给到 90s 稳妥
const READY_TIMEOUT: Duration = Duration::from_secs(90);
const READY_POLL: Duration = Duration::from_millis(250);

/// 壳自己的诊断日志。桌面应用出问题时这是第一现场，别省。
///
/// 路径刻意不用 `temp_dir()`：用户清理临时文件、绿色软件被移动、甚至某些
/// 启动方式下 TMP 环境变量异常，都会让日志写丢。`%LOCALAPPDATA%\GachaBench\logs\`
/// 是用户可见、可手删、清临时文件不会动的稳妥位置。
fn log_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("GachaBench").join("logs")
}

fn shell_log(msg: &str) {
    use std::io::Write;
    let dir = log_dir();
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("shell.log"))
    {
        let _ = writeln!(f, "{msg}");
    }
}

/// 弹一个原生消息框。窗口/webview 都还没建起来时，这是唯一能跟用户说话的方式。
#[cfg(windows)]
fn native_alert(title: &str, text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
    use std::os::windows::ffi::OsStrExt;
    fn wide(s: &str) -> Vec<u16> {
        std::ffi::OsStr::new(s)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }
    unsafe {
        MessageBoxW(
            core::ptr::null_mut(),
            wide(text).as_ptr(),
            wide(title).as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}

#[cfg(not(windows))]
fn native_alert(_title: &str, _text: &str) {}

fn main() {
    // 壳是无控制台窗口进程，panic 信息不落盘就等于什么都没发生。
    std::panic::set_hook(Box::new(|info| {
        shell_log(&format!("[panic] {info}"));
    }));

    // 先记一笔 webview 状态：Tauri 自己也会探一次，两边对不上时这里能看出
    // 到底是环境问题还是 Tauri 内部的问题。
    match wry::webview_version() {
        Ok(v) => shell_log(&format!("[boot] webview_version = {v:?}")),
        Err(e) => {
            shell_log(&format!("[boot] webview_version = Err({e:?})"));
            native_alert(
                "GachaBench 无法启动",
                "没有检测到 WebView2 运行时。\n\nGachaBench 的界面依赖 Microsoft Edge WebView2 Runtime。\n请先安装它，再重新打开本程序。",
            );
            std::process::exit(1);
        }
    }

    let builder = tauri::Builder::default()
        .manage(Mutex::new(Backend {
            child: None,
            job: None,
            log: log_dir().join("backend.log"),
        }))
        .setup(|app| {
            let handle = app.handle().clone();

            let exe = find_backend_exe(&handle).ok_or_else(|| {
                format!(
                    "找不到后端可执行文件 {BACKEND_EXE}。\
                     请确认 tauri.conf.json 的 bundle.resources 把 backend/ 一起打进了包。"
                )
            })?;

            let port = free_port();
            //  guard 要显式绑定：`state.lock().unwrap().log.clone()` 里的临时
            // MutexGuard 会被当成活到语句末尾，触发 E0597。
            let log = {
                let state = app.state::<Mutex<Backend>>();
                let guard = state.lock().unwrap();
                guard.log.clone()
            };

            let mut child = spawn_backend(&exe, port, &log)?;
            let job = put_child_in_kill_job(&mut child);
            shell_log(&format!(
                "[setup] 后端端口 {port}，Job Object {}",
                if job.is_some() { "已建立" } else { "建立失败（sidecar 可能被误杀）" }
            ));
            app.state::<Mutex<Backend>>().lock().unwrap().child = Some(child);
            app.state::<Mutex<Backend>>().lock().unwrap().job = job;

            // 单独线程等 ready 再导航：在 setup() 里阻塞会拖住窗口创建
            std::thread::spawn(move || {
                let ready = wait_ready(port);
                if let Some(win) = handle.get_webview_window("main") {
                    let url = if ready {
                        format!("http://127.0.0.1:{port}/")
                    } else {
                        // 起不来就回到启动页的失败态，那里有日志路径和重试按钮
                        "loading.html?failed=1".to_string()
                    };
                    if let Ok(u) = url.parse() {
                        let _ = win.navigate(u);
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                kill_backend(&mut window.app_handle().state::<Mutex<Backend>>().lock().unwrap());
            }
        });

    // build 失败（webview 创建不了等）时 expect() 只会留下一个空白窗口然后
    // 进程消失，用户完全无从判断。改成弹窗说人话 + 日志落盘。
    let app = match builder.build(tauri::generate_context!()) {
        Ok(app) => app,
        Err(e) => {
            shell_log(&format!("[fatal] Tauri 构建失败: {e}"));
            native_alert(
                "GachaBench 无法启动",
                &format!(
                    "窗口创建失败：{e}\n\n多数情况是 WebView2 运行时异常，\
                     重启电脑或重装 Microsoft Edge WebView2 Runtime 通常能解决。\n\n\
                     日志位置：\n{}",
                    log_dir().join("shell.log").display()
                ),
            );
            std::process::exit(1);
        }
    };
    app.run(|app, event| {
        // 兜底：所有窗口都关了 / 进程要退了，后端必须跟着走
        if let tauri::RunEvent::Exit = event {
            kill_backend(&mut app.state::<Mutex<Backend>>().lock().unwrap());
        }
    });
}

/// 在后端 exe 可能出现的几个位置里找它。
///
/// `bundle.resources` 的 glob 在不同 Tauri 版本下落出来的结构不完全一样，
/// 所以列候选 + 兜底递归，不把宝押在一个路径上。
fn find_backend_exe(app: &tauri::AppHandle) -> Option<PathBuf> {
    for rel in [
        format!("backend/{BACKEND_EXE}"),
        format!("resources/backend/{BACKEND_EXE}"),
        format!("../Resources/backend/{BACKEND_EXE}"),
    ] {
        if let Ok(p) = app.path().resolve(&rel, tauri::path::BaseDirectory::Resource) {
            if p.is_file() {
                return Some(p);
            }
        }
    }

    if let Ok(res_dir) = app.path().resource_dir() {
        return find_file_named(&res_dir, BACKEND_EXE, 6);
    }
    None
}

fn find_file_named(root: &PathBuf, name: &str, max_depth: usize) -> Option<PathBuf> {
    if max_depth == 0 {
        return None;
    }
    for e in std::fs::read_dir(root).ok()?.flatten() {
        let p = e.path();
        if p.is_file() && p.file_name().map(|n| n == name).unwrap_or(false) {
            return Some(p);
        }
        if p.is_dir() {
            if let Some(hit) = find_file_named(&p, name, max_depth - 1) {
                return Some(hit);
            }
        }
    }
    None
}

/// 找个空闲端口：绑一下 :0 让内核分配，拿到就立刻放开。
/// 后端启动时还会再 pick_port() 兜一次，两层保险。
fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8000)
}

fn spawn_backend(exe: &PathBuf, port: u16, log: &PathBuf) -> std::io::Result<Child> {
    let mut cmd = Command::new(exe);
    cmd.env("GACHABENCH_PORT", port.to_string())
        // 后端别自己开浏览器：窗口由壳控制
        .env("GACHABENCH_NO_BROWSER", "1")
        // PyInstaller onedir 的 exe 与 _internal/ 同级，固定 cwd 免得受用户工作目录影响
        .current_dir(exe.parent().unwrap_or(exe))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd.spawn()?;

    // 管道必须有人读：后端日志写满缓冲区会把整个服务堵死。
    // 顺手落一份日志到临时目录 —— 桌面应用出问题时这是唯一能看的地方。
    if let Some(out) = child.stdout.take() {
        let log = log.clone();
        std::thread::spawn(move || pump(BufReader::new(out), log));
    }
    if let Some(err) = child.stderr.take() {
        let log = log.clone();
        std::thread::spawn(move || pump(BufReader::new(err), log));
    }
    Ok(child)
}

fn pump<R: BufRead>(reader: R, log: PathBuf) {
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log)
    {
        for line in reader.lines().map_while(Result::ok) {
            let _ = writeln!(f, "{line}");
        }
    }
}

/// 把 sidecar 关进一个「壳死了它也必须死」的 Windows Job Object。
///
/// 为什么需要它：`on_window_event(CloseRequested)` 只能覆盖「用户点关闭按钮」。
/// 壳被任务管理器强杀、或者自己崩了的时候，那个回调不会触发，sidecar 就变成
/// 孤儿进程继续占着端口跑。Job Object 的 `KILL_ON_JOB_CLOSE` 语义是
/// 「job 的最后一个句柄关闭时，杀掉里面的所有进程」——而进程退出时系统会
/// 自动关掉它的所有句柄，所以**任何死法**都覆盖到了。
/// 副作用是白赚的：job 内的进程会继承归属，sidecar 拉的 ffmpeg 也一起端掉，
/// 不用再递归 taskkill。
#[cfg(windows)]
fn put_child_in_kill_job(child: &mut Child) -> Option<isize> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, SetInformationJobObject,
    };

    // JobObjectExtendedLimitInformation
    const INFO_CLASS: i32 = 9;

    unsafe {
        let job = CreateJobObjectW(core::ptr::null(), core::ptr::null());
        if job.is_null() {
            return None;
        }

        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = core::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        let ok = SetInformationJobObject(
            job,
            INFO_CLASS,
            &info as *const _ as *mut core::ffi::c_void,
            core::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 || AssignProcessToJobObject(job, child.as_raw_handle()) == 0 {
            CloseHandle(job);
            return None;
        }

        // 🔴 这里绝不 CloseHandle(job)。句柄必须活到壳进程退出，让系统去关它，
        // KILL_ON_JOB_CLOSE 才会触发。存进 state 只为把「这是故意留着的」写明白。
        Some(job as isize)
    }
}

#[cfg(not(windows))]
fn put_child_in_kill_job(_child: &mut Child) -> Option<isize> {
    None
}

/// 端口能连上就说明 lifespan 跑完了 —— uvicorn 是先 lifespan.startup()
/// 再 create_server()，所以「能连上」是精确的 ready 信号，不用 HTTP 探活
/// （也就不存在跨域问题）。
fn wait_ready(port: u16) -> bool {
    let deadline = Instant::now() + READY_TIMEOUT;
    let addr = format!("127.0.0.1:{port}");
    while Instant::now() < deadline {
        if let Ok(a) = addr.parse() {
            if TcpStream::connect_timeout(&a, READY_POLL).is_ok() {
                return true;
            }
        }
        std::thread::sleep(READY_POLL);
    }
    false
}

/// Windows 上 `Child::kill()` 只杀自己，ffmpeg 子进程会变孤儿。
/// 用 taskkill /T 把整棵树端掉。
///
/// 正常情况下 Job Object 已经兜住了（壳一死整棵树都没），这里只是双保险：
/// 用户点关闭按钮时我们想**立刻**收干净，不等进程退出。
fn kill_backend(backend: &mut Backend) {
    if let Some(mut child) = backend.child.take() {
        #[cfg(windows)]
        {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/PID", &child.id().to_string()])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
    // job 句柄仍然不关：留到壳进程自然退出，让 KILL_ON_JOB_CLOSE 收尾。
    backend.job = None;
}
