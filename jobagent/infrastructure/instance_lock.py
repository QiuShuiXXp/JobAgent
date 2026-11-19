"""单实例锁：桌面形态下一个运行目录只允许跑一个后端。

用操作系统的文件区域锁而不是 PID 文件：进程崩溃或被 taskkill 之后锁由内核回收，
不需要任何"上次是不是脏退出"的判断。抢不到锁就说明真有一个后端还活着，
再起一个只会让两边抢同一个 SQLite 和同一份 chrome_profile。
"""
import os

from jobagent.config import paths

# 锁句柄要活到进程结束，关掉就被内核当成释放
_handle: int | None = None


class AlreadyRunning(RuntimeError):
    """已有后端持有运行目录锁。"""


def acquire() -> str:
    """独占 lock_file()，返回锁文件路径；失败抛 AlreadyRunning。"""
    global _handle
    if _handle is not None:
        return str(paths.lock_file())
    lock = paths.lock_file()
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise AlreadyRunning(
            f"已有 JobAgent 在运行"
            "桌面端请只保留一个窗口；要先起新的就把现在这个关掉。") from None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _handle = fd
    return str(lock)
