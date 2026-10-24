# ghreport — служба сводки issue (Go)

Статус: реализованы базовые пакеты и транспортный слой; кэш, пагинация, сборка отчёта и `main` — в следующих этапах.

## Пакеты (`internal/`)

| Пакет | Назначение |
|-------|-----------|
| `args` | разбор и проверка командной строки |
| `config` | `GITHUB_API_BASE_URL`, `GITHUB_TOKEN` |
| `model` | структуры отчёта, JSON-вывод |
| `exitcode` | код возврата по результатам репозиториев |
| `clock` | часы (`Real`, `Fake`) — все паузы идут через них |
| `output` | атомарная запись отчёта |
| `linkhdr` | разбор заголовка `Link`, адрес `rel="next"` |
| `trace` | журнал HTTP-попыток (JSON Lines) |
| `httpx` | GET-запросы: заголовки, перенаправления, повторы, лимиты |

## HTTP-слой

Заголовки каждого запроса: `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2026-03-10`,
`If-None-Match` (если передан ETag), `Authorization: Bearer <token>` (если токен задан и адрес запроса имеет тот же
origin, что и `GITHUB_API_BASE_URL`; на другой хост токен не уходит никогда, в том числе при перенаправлении).

**User-Agent:** `ghreport/1.0 (+учебный проект; read-only сводка issue)` — константа `httpx.DefaultUserAgent`.

Таймаут одного запроса — 30 с, тело ответа ограничено 64 МиБ. Перенаправления 301/302/307 обрабатываются вручную
(не более 5, только http/https), каждый переход — отдельная запись журнала. Остальные 3xx, кроме 304, — ошибка.

| Ситуация | Поведение |
|----------|-----------|
| 200, 304 | успех |
| 401 | `ErrUnauthorized`, без повторов |
| 404 | `ErrNotFound` |
| 403/429 с признаками лимита | пауза (`retry-after`, иначе `x-ratelimit-reset`, иначе 60 с), до 3 повторов, затем `ErrRateLimited` |
| сеть, таймаут, 5xx | до 3 повторов с паузами 1, 2, 4 с, затем `ErrRequestFailed` |
| прочие 4xx (в т.ч. 403 без признаков лимита) | `ErrRequestFailed` без повторов |

Журнал (`--trace`): по строке на каждую HTTP-попытку; поле `error` всегда присутствует (`null`, `timeout`, `network`,
`canceled`, `too_large`); `from_cache` истинно только для 304 при отправленном `If-None-Match`; userinfo из адреса
удаляется, заголовок `Authorization` и токен в журнал не попадают.

## Проверки

```
cd service && gofmt -l . && go vet ./... && go test ./... -race -count=1
python tools/check_contracts.py service --tests service --check-covers
```
