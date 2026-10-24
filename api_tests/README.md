# api_tests — интеграционные тесты службы ghreport (pytest)

Тесты запускают собранный бинарник `ghreport` как отдельный процесс и проверяют наблюдаемое поведение: отчёт,
журнал `--trace`, коды возврата. Служба — чёрный ящик; сервер **неизвестен** (состав выдачи, число страниц и значения
полей заранее не известны). Зависимости: только `pytest >= 7` и стандартная библиотека (`requirements.txt`).

## Запуск

```
pip install -r api_tests/requirements.txt
export GHREPORT_BIN=/абсолютный/путь/к/ghreport
export GITHUB_API_BASE_URL=http://127.0.0.1:8765      # сервер, совместимый с контрактом части 2
pytest api_tests -q                                   # основной прогон
pytest api_tests -m selfcheck -q                      # мета-тесты самого набора (сервер не нужен)
```

Локальный сервер для пробного прогона: `python api_tests/selfcheck/stub_server.py --port 8765`.

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `GHREPORT_BIN` | Абсолютный путь к собранной службе (обязательна для основного прогона) |
| `GITHUB_API_BASE_URL` | Базовый адрес сервера (обязательна; без неё набор не стартует, чтобы служба не ушла на `api.github.com`) |
| `GITHUB_TOKEN` | Необязательный токен; тесты передают его службе и оракулу только на `GITHUB_API_BASE_URL` |
| `GHREPORT_TEST_REPOS` | Репозитории для тестов через запятую; по умолчанию `stub/alpha` |
| `GHREPORT_STRICT` | `1` — нехватка данных у сервера (нет второй страницы, PR, разброса дат) даёт **падение**, а не skip |
| `GHREPORT_TIMEOUT` | Таймаут одного запуска службы в секундах (по умолчанию 60) |

Без `GHREPORT_BIN`/`GITHUB_API_BASE_URL` фикстуры завершаются с понятным сообщением (без трассировки); сбор тестов и
мета-тесты работают без них.

## Три опоры набора

1. **Оракул** (`helpers/oracle.py`). Тесты сами читают выдачу сервера GET-запросами (только на `GITHUB_API_BASE_URL`,
   переходы по `Link rel="next"`, собственный разбор заголовка и дат) и независимо вычисляют ожидаемый отчёт:
   фильтр PR по ключу `pull_request`, `--since` (00:00 UTC, смещения учитываются как моменты времени),
   дедупликация по `number` (остаётся первое вхождение), `limit` в порядке выдачи сервера, затем сортировка
   `(created_at, number)`. Отчёт службы сверяется с этим результатом, а не с зашитыми числами.
2. **Прокси с подменой ответов** (`helpers/proxy.py`, фикстура `proxy`). `127.0.0.1:<порт>` пересылает GET на сервер,
   ведёт журнал запросов (метод, путь+запрос, заголовки, момент, признак пересечения по времени) и внедряет сбои
   правилами `proxy.add_rule(match, action, times)`: `respond` (429/5xx/401/битое тело), `drop_header`, `rewrite_body`,
   `replace_link`, `rewrite_link` (непрозрачный `cursor`: прокси дописывает случайный параметр в next-ссылку и снимает его
   при пересылке — так проверяется, что служба не конструирует адреса страниц), `redirect`, `reset_connection`,
   `add_delay`. Адреса сервера в `Link`/`Location` переписываются на адрес прокси — обойти его нельзя.
   Служба запускается с `GITHUB_API_BASE_URL=<proxy.url>`.
3. **Пробы контракта сервера** (`helpers/probes.py`, `test_server_contract.py`). Проверяют, что сам сервер соблюдает
   контракт; сообщение начинается с `SERVER VIOLATION:` — виноват сервер, а не служба.

## Структура

| Путь | Назначение |
|------|------------|
| `conftest.py` | Фикстуры `env`, `cache_dir` (свой для каждого теста), `oracle`, `proxy`, `ghreport`, `test_repos`, `profiles`, `need`, подбор репозиториев `multipage_repo`/`pr_repo`/`since_repo`/`limit_repo`/`plain_repo` |
| `helpers/runner.py` | `run_ghreport(repos, cache_dir, *args, ...) -> Result`, `report_of`, `trace_of`; всегда `--out -` и `timeout` |
| `helpers/oracle.py` | `OracleClient`, `parse_link_header`, `expected_issues`, `to_report_issue` |
| `helpers/proxy.py` | `FaultProxy`, правила и действия |
| `helpers/classify.py` | Классификатор репозиториев по данным оракула (кэш на сессию) |
| `helpers/env.py` | Проверка окружения, `need(condition, reason)` |
| `helpers/probes.py` | Пробы контракта сервера |
| `selfcheck/` | Поддельный сервер `stub_server.py` и мета-тесты (`-m selfcheck`) |

`run_ghreport` совместима с примером ТЗ: `run_ghreport("stub/alpha", tmp_path / "cache")`; `token=None` убирает токен,
по умолчанию он берётся из окружения; `report_of(result, allowed=(0, 1))` принимает код 1.

### Нехватка данных у сервера

`need(condition, reason)` (одноимённая фикстура): при невыполненном условии — `pytest.skip`, при `GHREPORT_STRICT=1` —
`pytest.fail`. Так набор работает с любым сервером, но в CI с известным сервером «тихий» пропуск невозможен.

## Поддельный сервер и мутанты

`selfcheck/stub_server.py` — совместимый с контрактом сервер (stdlib): `stub/alpha` (250 элементов: issue и PR, метки,
unicode в заголовках, смещения `Z` и `+03:00`), `stub/small` (3), `stub/empty` (0), прочие — 404; токен `invalid` — 401;
`per_page`/`page`, абсолютный `Link`, `ETag`/`If-None-Match` → 304. Флаги: `dup_items` — **допустимо** (повтор
элемента на стыке страниц); нарушения для мутантов: `drop_item_between_pages`, `pr_without_flag`, `always_304`,
`broken_json`, `reversed_sort`, `no_link`. Запуск: `python api_tests/selfcheck/stub_server.py --port 0 --flags no_link`.
Мета-тесты доказывают, что каждый мутант обнаруживается пробами.

## Принятые решения

См. [../docs/decisions.md](../docs/decisions.md), строки «api_tests: …».

## Проверки

```
pytest api_tests -m selfcheck -q
python tools/check_contracts.py api_tests --tests api_tests --check-covers
python tools/check_contracts.py api_tests/helpers --tests api_tests/selfcheck --check-covers   # строже: без самопокрытия
```
