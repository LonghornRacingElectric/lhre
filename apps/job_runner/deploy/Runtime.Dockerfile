FROM lhr/hil-fmu-bench:2026-11-14
COPY input /bench/input
COPY OrionFMI.fmu /bench/OrionFMI.fmu
COPY task.py /runner/task.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python3", "/runner/task.py", "cpu"]
