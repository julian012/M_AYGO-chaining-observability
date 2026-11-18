"""Publicación de métricas propias en CloudWatch (solo biblioteca estándar).

Usa la AWS CLI que trae Amazon Linux 2023 y las credenciales del perfil de instancia
(``LabInstanceProfile``); no se agregan dependencias (ADR-0003). Las métricas son de alta
resolución (1 s) para poder ver ventanas de 10 s en el tablero ``obsproto-pruebas``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

NAMESPACE = "ObsProto"
REGION = os.environ.get("AWS_REGION", "us-east-1")
_CHUNK = 500  # PutMetricData admite hasta 1000 valores por llamada


def datum(name: str, value: float, unit: str = "None", ts: Optional[float] = None, **dims: str) -> Dict[str, object]:
    return {
        "MetricName": name,
        "Value": float(value),
        "Unit": unit,
        "StorageResolution": 1,
        "Timestamp": datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat(),
        "Dimensions": [{"Name": k, "Value": str(v)} for k, v in dims.items()],
    }


def publish(data: List[Dict[str, object]], namespace: str = NAMESPACE) -> bool:
    """Envía los datos con ``aws cloudwatch put-metric-data``. Devuelve False si no se pudo."""
    if not data:
        return True
    aws = shutil.which("aws")
    if aws is None:
        print("[cloudwatch] no se encontró la AWS CLI; no se publican métricas")
        return False
    ok = True
    for i in range(0, len(data), _CHUNK):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data[i : i + _CHUNK], fh)
            path = fh.name
        try:
            res = subprocess.run(  # noqa: S603 - argumentos fijos, sin shell
                [
                    aws,
                    "cloudwatch",
                    "put-metric-data",
                    "--region",
                    REGION,
                    "--namespace",
                    namespace,
                    "--metric-data",
                    f"file://{path}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode != 0:
                ok = False
                print(f"[cloudwatch] error al publicar: {res.stderr.strip()[:300]}")
        finally:
            os.unlink(path)
    return ok
