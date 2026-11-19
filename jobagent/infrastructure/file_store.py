"""简历文件落盘：UUID 规整命名 + 明文存储 + 读/删。"""
import uuid
from pathlib import Path


class FileStore:
    def __init__(self, root: str) -> None:
        # 简历统一存放在 storage 根目录下的 resumes 子目录
        self.root = Path(root) / "resumes"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, raw: bytes, original_filename: str) -> str:
        """落盘原始字节，返回 file_ref（相对路径，供槽位 resume_file_ref 引用）。"""
        suffix = Path(original_filename).suffix.lower()
        # UUID 命名避免重名与原始文件名泄露，保留原后缀供解析时分派
        ref = f"{uuid.uuid4().hex}{suffix}"
        (self.root / ref).write_bytes(raw)
        return ref

    def load(self, file_ref: str) -> bytes:
        return (self.root / file_ref).read_bytes()

    def delete(self, file_ref: str) -> None:
        (self.root / file_ref).unlink(missing_ok=True)