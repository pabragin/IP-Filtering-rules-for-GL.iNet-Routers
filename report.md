# Отчёт сборки filter.txt

- Подсетей IPv4: **10577**, IPv6: **2395**
- Источники подсетей: ipverse (RIR) + BGP по RU-AS
- BGP добавил поверх ipverse: **1 203 712** адресов IPv4
- Доменов в источнике: **34**, оставлено в filter.txt: **32**, покрыто подсетями (исключено): **2**

## Исключённые домены (все адреса уже внутри подсетей)

- `3ebra.net` — 212.109.222.250
- `mkb.ru` — 93.92.113.12

## Оставленные домены (адреса вне подсетей — иностранный хостинг/CDN)

- `page.link`
- `steamcommunity.com`
- `steampowered.com`
- `steampipe.akamaized.net`
- `steambroadcast.akamaized.net`
- `battle.net`
- `blizzard.com`
- `battlenet.com.cn`
- `diablo3.com`
- `blzddist1-a.akamaihd.net`
- `blzddist2-a.akamaihd.net`
- `levenhuk.com`
- `weborama-tech.ru`
- `apple.com`
- `tiera.org`
- `bitrix24.com`
- `bxcdn.com`
- `plex.tv`
- `crashlytics.com`
- `doubleclick.net`
- `icloud.com`

## Не резолвятся (оставлены на всякий случай)

- `gismeteo.st`
- `rtbcdn.ru`
- `steam.com`
- `steamcontent.com`
- `steamserver.net`
- `steamstatic.com`
- `yandexcloud.net`
- `aaplimg.com`
- `cdn-apple.com`
- `halitsuman.com`
- `vk-analytics.ru`
