import base64
import os
import subprocess
import sys


POWERSHELL_NOTICE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if (-not [System.Windows.Forms.SystemInformation]::UserInteractive) { exit 1 }
$icon = New-Object System.Windows.Forms.NotifyIcon
try {
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Text = $env:COURSE_NOTICE_TITLE
    $icon.Visible = $true
    $kind = [System.Windows.Forms.ToolTipIcon]::Info
    if ($env:COURSE_NOTICE_FAILED -eq '1') {
        $kind = [System.Windows.Forms.ToolTipIcon]::Warning
    }
    $icon.ShowBalloonTip(3500, $env:COURSE_NOTICE_TITLE, $env:COURSE_NOTICE_BODY, $kind)
    for ($i = 0; $i -lt 20; $i++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 200
    }
} finally {
    $icon.Dispose()
}
"""


def notify(title, message, failed=False):
    if sys.platform != "win32":
        return
    environment = dict(os.environ, COURSE_NOTICE_TITLE=title, COURSE_NOTICE_BODY=message[:240],
                       COURSE_NOTICE_FAILED=str(int(failed)))
    encoded = base64.b64encode(POWERSHELL_NOTICE.encode("utf-16le")).decode("ascii")
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        print("系统通知未发送，请查看终端输出。", file=sys.stderr)
