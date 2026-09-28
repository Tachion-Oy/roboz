# SafeScripts

`SafeScripts` adds one `run_shell_script` tool. Omit `script` to list trusted
`.sh` files and their leading comments; give a path relative to the script
directory to run one. Scripts take no arguments or interactive input. Output is
streamed and included in the result.

## Run in the application

Configure `SafeScripts(scripts_dir=Path("/opt/trusted-scripts"))` on an agent with a
`Sandbox`. The sandbox root is the script's working directory. Local execution
requires Bash and POSIX process groups and uses the application's privileges.

## Run on a Linux host

Start one service in a dedicated host process:

```sh
python -m roboz.shed.tools.safe_scripts serve \
  --socket /run/user/1000/roboz-scripts/scripts.sock \
  --scripts /opt/trusted-scripts --cwd /srv/workspace
```

The command stays in the foreground. `--timeout-s`, `--max-output-bytes`, and
repeatable `--allow-env NAME` options set host policy. Defaults are 300 seconds,
65,536 output bytes, and no forwarded host variables. The service needs Bash;
its scripts run with the serving user's privileges.

Configure the client with `SafeScripts(socket_path=Path("/path/to/scripts.sock"))`.
For a container client, mount the socket directory and use its path inside the
container. Script and working-directory paths are resolved on the host.

Check the service without listing or running scripts:

```sh
python -m roboz.shed.tools.safe_scripts check \
  --socket /run/user/1000/roboz-scripts/scripts.sock
```

`check` returns `0` for a compatible service and `1` otherwise. `serve` returns
`0` after normal shutdown, `1` on failure, and `2` for invalid arguments or
policy. `--help` works on every supported Python platform.

## Trust and failure behavior

Install reviewed scripts outside agent-writable directories. Symlink scripts and
directories are refused. File-tool permissions do not limit scripts. The socket's
parent must be owned by the serving user with mode `0700`; the socket is `0600`.
The service refuses an existing socket path, including a stale one.

Cancellation and SIGINT/SIGTERM stop active script process groups. A broken
connection can leave the execution outcome unknown, so clients do not retry
automatically. After upgrading, run `check` to verify that the client and service
speak a compatible protocol.
