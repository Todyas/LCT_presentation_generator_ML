from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path


class ExportTimeoutError(Exception):
    pass


class ExportFailedError(Exception):
    pass


async def convert_to_pdf(pptx_path: str, output_dir: str, timeout_s: int = 60) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"

    proc = await asyncio.create_subprocess_exec(
        "soffice",
        "--headless",
        f"-env:UserInstallation=file://{profile_dir.as_posix()}",
        "--convert-to",
        "pdf",
        "--outdir",
        output_dir,
        pptx_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ExportTimeoutError(
            f"soffice conversion of {pptx_path} exceeded {timeout_s}s"
        )

    expected_pdf = Path(output_dir) / (Path(pptx_path).stem + ".pdf")
    if proc.returncode != 0 or not expected_pdf.exists():
        raise ExportFailedError(
            f"soffice failed (code={proc.returncode}): {stderr.decode(errors='replace')}"
        )
    return str(expected_pdf)
