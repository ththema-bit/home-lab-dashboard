# home-landing

A static landing page for the services running on your home network, with a status check
that runs from cron. No framework, no build step. The page itself is plain HTML/JS, the
backend is a single Python script that writes `status.json`.

## Features

- Services grouped in `services.json`, each with name, description, URL, optional `check` URL, alternative address and icon
- Green/red status light per service, updated from `status.json`
- Search: `/` focuses, Enter opens the first match, Esc clears
- Optional list of network shares with a copy button
- Auto-discovery: lists listening TCP ports on the local host (and over SSH on other hosts, if configured), probes unknown ones over HTTP and shows web UIs that are not in your list as "Newly discovered"
- Self-description: a tool can serve `/.well-known/hub.json` (`{"name","desc","group","icon"}`) to be placed into a group automatically
- Links are rewritten to the host address in the browser's subnet when a host has several IPs
- Fonts (Barlow Condensed, IBM Plex Mono, SIL OFL) are bundled, nothing is loaded from the internet

## Requirements

- Python 3.9+ (standard library only), `ss` and `ip` (iproute2)
- A web server for static files (nginx example in `nginx-home.conf`)
- Optional: `ufw` with passwordless `sudo -n ufw status` (used to mark ports that are blocked from the LAN), SSH key access to other hosts

## Setup

```bash
cp services.example.json services.json    # then edit it
./deploy.sh /var/www/home                 # copies files and writes status.json
```

`deploy.sh` copies `index.html`, `services.json` and the fonts to the target directory and runs
`status.py` once. Add the status job to cron:

```
* * * * * /usr/bin/python3 /path/to/home-landing/status.py /var/www/home/status.json
```

Serve the directory with nginx (`nginx-home.conf` is a minimal site) or any static server.

## Configuration

`services.json` (see `services.example.json`):

- `hosts`: map of host name to IP, e.g. `{"Server": "127.0.0.1"}`. The first entry is treated as the local machine. Items refer to it with `"host"`.
- `groups[]`: `id`, `name`, `items[]` (`name`, `desc`, `url`, optional `check`, `alt`, `host`, `icon`), optional `shares` (`[name, UNC path]` pairs)
- Icons: `play`, `book`, `down`, `folder`, `spark`, `nodes`, `term`, `grid`, `orbit`, `chart`, `net`, `gauge`, `screen`, `lock`, `globe`

`ignore.json`: ports that auto-discovery should not report, as `HostName:port`.

Environment variables for `status.py`: `HOME_SSH_USER` (enables discovery on non-local hosts over SSH) and `HOME_SSH_KEY` (default `~/.ssh/id_ed25519`).

The page UI is in English. The status script talks to every URL listed and probes open ports on your hosts, so only run it on networks you control.

## License

MIT for the code. The bundled fonts are under the SIL Open Font License 1.1.
