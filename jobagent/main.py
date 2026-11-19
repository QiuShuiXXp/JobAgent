
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("JOBAGENT_DESKTOP", "false")   # 必须早于 settings 的导入
    for _stream in (sys.stdout, sys.stderr):              # Windows 终端默认 GBK，中文日志会乱码
        _stream.reconfigure(encoding="utf-8", errors="replace")

    import uvicorn

    from jobagent.config.settings import settings

    print(f"读的配置 = {settings.model_config['env_file']} | "
          f"监听 http://{settings.app_host}:{settings.app_port} | "
          f"库 = {settings.data_base_url.split('://', 1)[0]}", flush=True)
    uvicorn.run(app="jobagent.api.app:app", host=settings.app_host, port=settings.app_port)
