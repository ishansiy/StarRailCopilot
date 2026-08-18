import subprocess


def shell_with_check_okay(
    device,
    cmdargs,
    stream=False,
    timeout=None,
    rstrip=True,
):
    """Run an adbutils shell command without dropping its transport timeout."""
    if isinstance(cmdargs, (list, tuple)):
        cmdargs = subprocess.list2cmdline(cmdargs)
    connection = device.open_transport(timeout=timeout)
    connection.send_command("shell:" + cmdargs)
    connection.check_okay()
    if stream:
        return connection
    output = connection.read_until_close()
    return output.rstrip() if rstrip else output
