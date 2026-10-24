# api_tests — интеграционные тесты службы ghreport (pytest)

## Назначение

Набор проверяет службу `ghreport` как **чёрный ящик**: запускает собранный бинарник отдельным процессом и сверяет
наблюдаемое поведение — отчёт, журнал `--trace`, коды возврата, заголовки запросов. Сервер **неизвестен**: состав
выдачи, число страниц и значения полей заранее не известны, поэтому ожидаемый результат всегда вычисляется самим
набором (оракул), а не берётся из констант. Набор не только подтверждает успешный сценарий, но и обнаруживает нарушения
контракта (ТЗ 5.4) — это доказывается мутационной проверкой (см. ниже). Зависимости: `pytest >= 7` и стандартная
библиотека (`requirements.txt`).

## Запуск

```
pip install -r api_tests/requirements.txt
export GHREPORT_BIN=/абсолютный/путь/к/ghreport
export GITHUB_API_BASE_URL=http://127.0.0.1:8765      # сервер, совместимый с контрактом части 2
pytest api_tests -q          # основной прогон; добавьте -rs, чтобы увидеть причины skip
pytest api_tests -m selfcheck -q   # мета-тесты самого набора (сервер и бинарник не нужны)
```

Локальный сервер для пробного прогона: `python api_tests/selfcheck/stub_server.py --port 8765`.
Полный прогон против Go-службы и stub занимает около 35 с (из них около 18 с — реальные паузы службы 1/2/4 с в трёх
тестах с меткой `slow`).

## Переменные окружения

| Переменная | Обязательна | Назначение |
|------------|-------------|------------|
| `GHREPORT_BIN` | да (по ТЗ) | Абсолютный путь к собранной службе |
| `GITHUB_API_BASE_URL` | да (по ТЗ) | Базовый адрес сервера, совместимого с контрактом части 2. Без неё набор не стартует, чтобы служба не ушла на `api.github.com` |
| `GITHUB_TOKEN` | нет (по ТЗ) | Токен; передаётся оракулу и (если тест не отключает его) службе только на `GITHUB_API_BASE_URL` |
| `GHREPORT_TEST_REPOS` | нет (доп.) | Репозитории для тестов через запятую; по умолчанию `stub/alpha` |
| `GHREPORT_STRICT` | нет (доп.) | `1` — нехватка данных у сервера (нет второй страницы, PR, разброса дат) даёт **падение**, а не skip |
| `GHREPORT_TIMEOUT` | нет (доп.) | Таймаут одного запуска службы в секундах (по умолчанию 60) |

Без обязательных переменных фикстуры завершаются понятным сообщением без трассировки (в отчёте pytest это error на
setup); сбор тестов и мета-тесты работают и без них.

## Архитектура: три опоры

1. **Оракул** (`helpers/oracle.py`). Тесты сами читают выдачу сервера GET-запросами (только на `GITHUB_API_BASE_URL`,
   переходы по `Link rel="next"`, собственный разбор заголовка и дат, независимый от службы) и вычисляют ожидаемый
   отчёт: фильтр PR по ключу `pull_request`, `--since` (00:00 UTC, смещения как моменты времени), дедупликация по
   `number` (остаётся первое вхождение), `limit` в порядке выдачи сервера, затем сортировка `(created_at, number)`.
2. **Прокси с подменой ответов** (`helpers/proxy.py`, фикстуры `proxy`, `other_proxy`; свой экземпляр на тест).
   `127.0.0.1:<порт>` пересылает GET на сервер, ведёт журнал (метод, путь+запрос, заголовки запроса и ответа, момент,
   признак пересечения по времени) и внедряет сбои правилами `proxy.add_rule(match, action, times)`: `respond`
   (429/5xx/401/битое тело), `drop_header`, `put_header`, `rewrite_body`, `replace_link`, `rewrite_link` (непрозрачный
   `cursor`: так проверяется, что служба не конструирует адреса страниц), `redirect`, `reset_connection`, `add_delay`.
   Адреса сервера в `Link`/`Location` переписываются на адрес прокси — обойти его нельзя.
3. **Пробы контракта сервера** (`helpers/probes.py`, `test_00_server_contract.py`) проверяют сам сервер.

Остальное: `helpers/runner.py` (запуск процесса, всегда с `timeout`, без `sleep`), `helpers/expect.py` (сравнение с
оракулом, запросы журнала), `helpers/classify.py` (подбор репозиториев по данным оракула: многостраничный, с PR,
с разбросом дат), `helpers/env.py` (`need(condition, reason)`: skip, а при `GHREPORT_STRICT=1` — fail).

## Как читать падения

* Сообщение начинается с **`SERVER VIOLATION:`** — виноват **сервер** (нарушен контракт выдачи), службу не трогайте.
* Любое другое падение — расхождение службы с оракулом или с ТЗ. В docstring каждого теста указано, какое требование
  он проверяет и какое нарушение ловит. Сообщения `assert_repo_matches` показывают номера issue отчёта и ожидаемые.
* `skip` всегда содержит причину («server data insufficient: …»); `pytest -rs` печатает их все.
  Если запуск должен быть исчерпывающим, задайте `GHREPORT_STRICT=1`.

## Карта «пункт 5.3 → файл → какие нарушения ловит»

| Пункт ТЗ | Файл | Ловит |
|----------|------|-------|
| 5.4 (контракт сервера) | `test_00_server_contract.py`, `test_server_contract.py` | PR без `pull_request`, невалидный JSON, обратный порядок, потерянный элемент между страницами, неполные страницы, нет `Link` при наличии следующей страницы, 304 без `If-None-Match` |
| 5.3.1 страницы и `--limit` | `test_01_pagination_limit.py` | остановка на конце страницы, `limit+1`, лишние запросы после `limit`, дубли на стыке страниц (и расход `limit` на дубль), ручное построение `page=N`, потеря последнего элемента страницы |
| 5.3.2 фильтр PR | `test_02_pr_filter.py` | PR в отчёте, `/pull/` в url, `count` с PR, проверка значения вместо наличия ключа (`pull_request: null`) |
| 5.3.3 `--since` | `test_03_since.py` | обратное сравнение, `<=` вместо `<` (issue ровно в 00:00:00), параметр `since` в запросах API, фильтр после `limit`, потеря самого раннего дня |
| 5.3.4 кэш | `test_04_cache.py` | нет `If-None-Match`, неверный ETag, ETag не обновляется по новому 200, `from_cache` неверен, потеря `Link` из кэша, падение на повреждённых файлах, токен в кэше, общий кэш разных каталогов |
| 5.3.5 лимиты | `test_05_rate_limit.py` | нет паузы при `retry-after`, 403 не распознан как лимит, обычный 403 ждёт и повторяется, `x-ratelimit-reset` |
| 5.3.6 ошибки | `test_06_errors.py` | 401 не фатален, 404 ≠ `not_found`, `all_failed` без кода 3, битый JSON/объект вместо массива принят, число повторов 5xx/обрыва и паузы 1+2+4 с, ошибки аргументов |
| 5.3.7 схема | `test_07_report_schema.py` | ключи и типы, порядок и дубликаты репозиториев, сортировка (в т.ч. только по number / без tie-break), поля issue, Unicode, `--out`, лишние файлы при `--out -`, `labels: null` |
| 5.3.8 журнал | `test_08_trace.py` | поля и типы, `attempt`, порядок `ts`, соответствие журналу прокси, Authorization/токен в журнале, файл без `--trace` |
| 4.4.1, 4.7 запрос | `test_09_request_format.py` | заголовки (`User-Agent`, версия API, `Accept`), Bearer, параллельные запросы, редиректы 301/302/307, токен на другой origin, утечка токена в вывод |

## Мутационная проверка набора

`python api_tests/selfcheck/run_mutants.py` (`--only ID`, `--service-dir service`, `--validate` — только проверить, что
каждый шаблон мутации находит ровно одно совпадение). Скрипт:

1. собирает немутированную службу и прогоняет набор против совместимого stub — должно быть зелёное, иначе остановка (код 2);
2. запускает stub с каждым флагом нарушения (6 мутантов сервера) — набор обязан падать;
3. запускает stub с совместимым отклонением `dup_items` — набор обязан остаться **зелёным**;
4. для каждого из 37 мутантов службы копирует `service/` во временный каталог, применяет правку (шаблон должен найтись
   ровно один раз, иначе мутант «устарел» и запуск падает), собирает бинарник и прогоняет `pytest api_tests -x -m "not selfcheck"`.

Рабочий репозиторий не меняется. Код возврата 0 — убиты **все** мутанты. Не мутировано: параллельные запросы (цикл
выборки последовательный по построению, одной правкой многопоточность не вводится); сам запрет проверен тестом
`test_requests_are_strictly_sequential`.

Итоговая таблица последнего прогона:

```
mutant                          kind        status  sec   first failing test / note
server:drop_item_between_pages  server      KILLED  1.0   test_00_server_contract.py::test_no_item_is_lost_between_pages
server:pr_without_flag          server      KILLED  0.5   test_00_server_contract.py::test_pull_requests_carry_the_pull_request_key
server:always_304               server      KILLED  0.5   test_00_server_contract.py::test_unconditional_requests_never_get_304
server:broken_json              server      KILLED  0.4   test_00_server_contract.py::test_pull_requests_carry_the_pull_request_key
server:reversed_sort            server      KILLED  0.5   test_00_server_contract.py::test_created_at_is_ascending_across_pages
server:no_link                  server      KILLED  0.5   test_00_server_contract.py::test_link_next_is_present_whenever_a_next_page_exists
compatible:dup_items            compatible  GREEN   32.1  -
skip_last_item_of_page          service     KILLED  1.5   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
limit_plus_one                  service     KILLED  1.6   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
pr_not_filtered                 service     KILLED  1.5   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
pr_null_counts_as_issue         service     KILLED  2.7   test_02_pr_filter.py::test_pull_request_with_null_value_is_still_a_pull_request
since_boundary_excluded         service     KILLED  2.9   test_03_since.py::test_since_inside_the_range_matches_the_oracle
since_inverted                  service     KILLED  2.9   test_03_since.py::test_since_inside_the_range_matches_the_oracle
since_sent_to_api               service     KILLED  3.5   test_03_since.py::test_the_since_parameter_is_not_sent_to_the_api
sort_reversed                   service     KILLED  1.5   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
sort_by_number_only             service     KILLED  29.1  test_07_report_schema.py::test_sorting_uses_created_at_before_number
sort_without_tiebreak           service     KILLED  28.5  test_07_report_schema.py::test_equal_created_at_is_ordered_by_number
dedup_disabled                  service     KILLED  2.0   test_01_pagination_limit.py::test_duplicate_at_the_page_boundary_is_counted_once
pages_built_by_hand             service     KILLED  2.3   test_01_pagination_limit.py::test_service_requests_exactly_the_page_addresses_it_was_given
link_next_ignored               service     KILLED  1.5   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
if_none_match_never_sent        service     KILLED  4.2   test_04_cache.py::test_second_run_revalidates_every_request_with_the_stored_etag
from_cache_always_false         service     KILLED  4.4   test_04_cache.py::test_second_run_revalidates_every_request_with_the_stored_etag
cache_never_written             service     KILLED  4.5   test_04_cache.py::test_second_run_revalidates_every_request_with_the_stored_etag
cache_not_refreshed             service     KILLED  6.9   test_04_cache.py::test_a_fresh_200_replaces_the_stored_etag
cache_drops_link                service     KILLED  4.2   test_04_cache.py::test_second_run_revalidates_every_request_with_the_stored_etag
retry_after_no_pause            service     KILLED  7.3   test_05_rate_limit.py::test_429_with_retry_after_is_waited_out
rate_limit_403_not_recognised   service     KILLED  8.2   test_05_rate_limit.py::test_403_limit_signals_are_waited_out
plain_403_is_rate_limit         service     KILLED  41.6  test_05_rate_limit.py::test_plain_403_is_not_a_rate_limit
unauthorized_is_request_failed  service     KILLED  11.5  test_06_errors.py::test_401_is_fatal_with_exit_code_3
not_found_is_request_failed     service     KILLED  12.5  test_06_errors.py::test_missing_repository_among_existing_ones
all_failed_not_fatal            service     KILLED  12.4  test_06_errors.py::test_all_repositories_missing_is_fatal
labels_null                     service     KILLED  1.5   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
author_dropped                  service     KILLED  1.5   test_01_pagination_limit.py::test_limit_smaller_than_the_listing_spans_pages
authorization_in_trace          service     KILLED  29.2  test_08_trace.py::test_no_credentials_in_the_trace
token_printed_to_stderr         service     KILLED  11.7  test_06_errors.py::test_401_is_fatal_with_exit_code_3
token_sent_cross_origin         service     KILLED  31.5  test_09_request_format.py::test_authorization_is_not_sent_to_another_origin_after_a_redirect
trace_attempt_zero              service     KILLED  8.1   test_05_rate_limit.py::test_429_with_retry_after_is_waited_out
out_stdout_also_file            service     KILLED  29.3  test_07_report_schema.py::test_report_to_stdout_creates_no_files_in_the_working_directory
repos_duplicates_kept           service     KILLED  27.2  test_07_report_schema.py::test_repository_order_follows_the_argument_and_duplicates_are_dropped
user_agent_missing              service     KILLED  29.3  test_09_request_format.py::test_required_request_headers
api_version_wrong               service     KILLED  29.3  test_09_request_format.py::test_required_request_headers
retries_reduced                 service     KILLED  14.4  test_06_errors.py::test_persistent_failures_become_request_failed_after_three_retries
redirect_307_not_followed       service     KILLED  31.3  test_09_request_format.py::test_redirects_are_followed_by_location[307]
invalid_json_accepted           service     KILLED  12.8  test_06_errors.py::test_invalid_response_bodies[broken_json]

total 44 - green: 1, killed: 43

baseline: немутированная служба + stub — зелёный (32 с); код возврата скрипта 0; полный прогон ≈ 15 мин.
```

## Ограничения

* Нет данных у сервера (одна страница, нет PR, одна дата, нет Unicode/ETag) → соответствующие тесты `skip` с причиной
  (`GHREPORT_STRICT=1` превращает skip в fail).
* Набор не видит того, что не отражается в трафике, выводе и файлах: внутреннюю структуру кэша (проверяется только
  поведение), работу с временем службы вне пауз (проверяются лишь **нижние** границы), реальные таймауты 30 с на запрос.
* Граница `--since` для issue, у которых локальная дата отличается от UTC-даты, зависит от данных сервера и на
  встроенном stub не проверяется.
* Эквивалентные мутации (не меняющие наблюдаемого поведения), например экранирование `<`/`>` в JSON, не вводились.
* Сигналы (SIGINT/SIGTERM, код 130) и запись в недоступный `--out` набор не проверяет (юнит-тесты службы на Go).

## Структура

| Путь | Назначение |
|------|------------|
| `conftest.py` | Фикстуры `env`, `cache_dir`, `oracle`, `proxy`, `other_proxy`, `ghreport`, `test_repos`, `profiles`, `need`, подбор репозиториев `multipage_repo`/`pr_repo`/`since_repo`/`limit_repo`/`plain_repo` |
| `helpers/` | Запуск, оракул, прокси, пробы, классификатор, окружение, `expect` |
| `test_00…test_09*.py` | Тесты службы (см. карту выше) |
| `selfcheck/` | Поддельный сервер `stub_server.py` (флаги `dup_items` — допустимо; `drop_item_between_pages`, `pr_without_flag`, `always_304`, `broken_json`, `reversed_sort`, `no_link` — нарушения), `run_mutants.py` и мета-тесты (`-m selfcheck`) |

## Принятые решения

См. [../docs/decisions.md](../docs/decisions.md), строки «api_tests: …».

## Проверки

```
pytest api_tests -m selfcheck -q
python tools/check_contracts.py api_tests --tests api_tests --check-covers
python tools/check_contracts.py api_tests/helpers --tests api_tests/selfcheck --check-covers   # строже: без самопокрытия
python api_tests/selfcheck/run_mutants.py
```
