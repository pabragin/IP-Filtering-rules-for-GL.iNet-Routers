#!/usr/bin/env python3
"""Сборка filter.txt для GL.iNet VPN Policy (Subscription URL).

Шаги:
1. Скачивает актуальные российские подсети IPv4/IPv6 (данные RIPE через ipverse).
2. Добавляет ручные подсети из data/custom-subnets.txt и агрегирует всё.
3. Резолвит каждый домен из data/domains.txt:
   - все адреса внутри итоговых подсетей -> домен покрыт, в filter.txt не пишется;
   - хотя бы один адрес снаружи (или домен не резолвится) -> домен остаётся.
4. Пишет filter.txt (домены + IPv4 + IPv6, по одной записи на строку)
   и report.md с результатами проверки покрытия.
"""

import ipaddress
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RU_IPV4_URL = "https://raw.githubusercontent.com/ipverse/country-ip-blocks/master/country/ru/ipv4-aggregated.txt"
RU_IPV6_URL = "https://raw.githubusercontent.com/ipverse/country-ip-blocks/master/country/ru/ipv6-aggregated.txt"
DNS_TIMEOUT = 5
DNS_WORKERS = 16


def fetch_lines(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "gl-inet-filter-build"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode()
    lines = [l.strip() for l in text.splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def read_list(path: Path) -> list[str]:
    lines = [l.strip() for l in path.read_text().splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def resolve(domain: str) -> list[ipaddress._BaseAddress] | None:
    """A- и AAAA-записи домена; None — домен не резолвится."""
    socket.setdefaulttimeout(DNS_TIMEOUT)
    try:
        infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.timeout, OSError):
        return None
    return sorted({ipaddress.ip_address(i[4][0]) for i in infos}, key=str)


def main() -> int:
    ru_v4 = [ipaddress.ip_network(l) for l in fetch_lines(RU_IPV4_URL)]
    ru_v6 = [ipaddress.ip_network(l) for l in fetch_lines(RU_IPV6_URL)]
    if len(ru_v4) < 1000 or len(ru_v6) < 100:
        print(f"Подозрительно короткие списки: v4={len(ru_v4)}, v6={len(ru_v6)}", file=sys.stderr)
        return 1

    custom = [ipaddress.ip_network(l) for l in read_list(ROOT / "data" / "custom-subnets.txt")]
    nets_v4 = sorted(ipaddress.collapse_addresses(ru_v4 + [n for n in custom if n.version == 4]))
    nets_v6 = sorted(ipaddress.collapse_addresses(ru_v6 + [n for n in custom if n.version == 6]))

    def covered(ip: ipaddress._BaseAddress) -> bool:
        nets = nets_v4 if ip.version == 4 else nets_v6
        return any(ip in n for n in nets)

    domains = read_list(ROOT / "data" / "domains.txt")
    with ThreadPoolExecutor(DNS_WORKERS) as pool:
        resolved = dict(zip(domains, pool.map(resolve, domains)))

    kept, dropped, unresolved = [], [], []
    for d in domains:
        ips = resolved[d]
        if ips is None:
            unresolved.append(d)
            kept.append(d)
        elif all(covered(ip) for ip in ips):
            dropped.append((d, ips))
        else:
            kept.append(d)

    out = kept + [str(n) for n in nets_v4] + [str(n) for n in nets_v6]
    (ROOT / "filter.txt").write_text("\n".join(out) + "\n")

    report = [
        "# Отчёт сборки filter.txt",
        "",
        f"- Подсетей IPv4: **{len(nets_v4)}**, IPv6: **{len(nets_v6)}**",
        f"- Доменов в источнике: **{len(domains)}**, оставлено в filter.txt: **{len(kept)}**,"
        f" покрыто подсетями (исключено): **{len(dropped)}**",
        "",
        "## Исключённые домены (все адреса уже внутри подсетей)",
        "",
    ]
    report += [f"- `{d}` — {', '.join(map(str, ips))}" for d, ips in dropped] or ["_нет_"]
    report += ["", "## Оставленные домены (адреса вне подсетей — иностранный хостинг/CDN)", ""]
    report += [f"- `{d}`" for d in kept if d not in unresolved]
    report += ["", "## Не резолвятся (оставлены на всякий случай)", ""]
    report += [f"- `{d}`" for d in unresolved] or ["_нет_"]
    (ROOT / "report.md").write_text("\n".join(report) + "\n")

    print(f"filter.txt: {len(out)} строк ({len(kept)} доменов, {len(nets_v4)} IPv4, {len(nets_v6)} IPv6)")
    print(f"Исключено доменов, покрытых подсетями: {len(dropped)}; не резолвятся: {len(unresolved)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
