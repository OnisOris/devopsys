
def render_systemd_unit(name: str, user: str, workdir: str, exec_start: str) -> str:
    unit = f"""[Unit]
Description={name}
After=network.target

[Service]
WorkingDirectory={workdir}
ExecStart={exec_start}
Restart=always
RestartSec=5
User={user}

[Install]
WantedBy=multi-user.target
"""
    return unit
