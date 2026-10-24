# Соответствие ТЗ

Аудит выполнен на этапе 4 по `docs/spec_part1.md`, `docs/spec_part2.md`, `docs/spec_part2_tables.md`,
`docs/spec_part3.md`, `docs/spec_appendix_a.md`. Статусы: **выполнено** — реализовано и проверено тестом;
**решение** — неоднозначность ТЗ, выбранный вариант записан в [decisions.md](decisions.md); **ограничение** — известное
отклонение или непроверяемое в этой среде. Все проверки воспроизводятся командой `python tools/verify_all.py`.

В файлах спецификации нет текста раздела 6 (последний пункт — 5.6 и приложения А–Г), поэтому он не аудируется;
формальные требования к сдаче собраны в `docs/submission_email.md`.

Условные обозначения: `Cxx` — тесты C++ (`msgfmt/tests/test_*.cpp`, имя теста после двоеточия); `Go` — пакеты
`service/internal/...`; `Py` — `api_tests/...`.

## Часть 1. msgfmt

| Пункт ТЗ | Где реализовано | Тесты | Статус |
|---|---|---|---|
| 3.2 состав: `include/`, `src/`, `tests/`, `CMakeLists.txt`, `README.md` | `msgfmt/` | — (структура каталога) | выполнено |
| 3.2 C++17, только стандартная библиотека | `msgfmt/CMakeLists.txt` (`CMAKE_CXX_STANDARD 17`, зависимостей нет) | сборка в `verify_all` (`msgfmt-release`) | выполнено |
| 3.2 цель `msgfmt` и тесты в CTest | `msgfmt/CMakeLists.txt`: `msgfmt_add_test`, `contracts_lint` | `ctest`: 14 тестов | выполнено |
| 3.2 команды сборки `cmake -S msgfmt -B build -DCMAKE_BUILD_TYPE=Release` / `cmake --build build` / `ctest --test-dir build` | README (корневой и `msgfmt/README.md`) | `verify_all`: шаг `msgfmt-release` | выполнено |
| 3.2 `-Wall -Wextra` без предупреждений | `CMakeLists.txt`: `msgfmt_apply_flags` (`-Wall -Wextra`, при `MSGFMT_WERROR=ON` ещё `-Werror`) | `verify_all`: сборка Release с `-DMSGFMT_WERROR=ON` (ноль предупреждений, иначе сборка падает) | выполнено |
| 3.2 санитайзеры `address,undefined`, ни одного сообщения | `MSGFMT_SANITIZE=ON`: `-fsanitize=address,undefined -fno-sanitize-recover=all` | `verify_all`: шаг `msgfmt-sanitize` (`ctest -V` сканируется на маркеры санитайзеров); выполнен в Fedora/WSL (g++ 14) — все 14 тестов зелёные. На Windows (mingw) шаг пропускается автоматически | выполнено (на Windows — skip, см. decisions.md) |
| 3.3 вход — байты UTF-8, нормализации нет, управляющие символы — обычный текст | `src/utf8.cpp`: `decode_utf8`, `decode_one` | `test_utf8`: `decode_nfc_and_nfd_differ`, `decode_empty_nul_bom_and_controls`; `test_cli_process`: `process_keeps_crlf_and_nul_bytes` | выполнено |
| 3.3 некорректный UTF-8 — единственная ошибка | `decode_one` (строгий декодер) | `test_utf8`: `decode_rejects_*` (стартовые байты, overlong, суррогаты, > U+10FFFF, обрывы) | выполнено |
| 3.4 единицы UTF-16, суррогатные пары | `utf8.cpp`: `utf16_units`, `utf16_length` | `test_utf8`: `utf16_units_boundaries`, `utf16_length_sums_units`; пример 10, 11 | выполнено |
| 3.4 служебные символы `* _ \` [ ] ( ) \` | `escape.cpp`: `is_service_char` | `test_escape`: `is_service_char_exactly_eight`, `is_service_char_agrees_with_parser_is_escapable` | выполнено |
| 3.5.1 однопроходный разбор слева направо | `parser.cpp`: `parse_slice` | `test_parser`: `rule1_*` | выполнено |
| 3.5.2 открывающие маркеры `**`, `_`, `` ` ``, `[` | `parse_slice` | `rule2_each_marker_opens_its_style`, `rule2_single_star_is_not_a_marker` | выполнено |
| 3.5.3 `_` на границе слова | `parse_slice`, `charclass.cpp`: `is_letter_or_digit` | `rule3_*`, `test_charclass`: `letter_digit_representatives_per_script`, `..._both_sides_of_every_table_boundary` | решение (определение «буква/цифра») |
| 3.5.4 после `**`, `` ` ``, `[` — непробельный символ | `parse_slice` | `rule4_*` | выполнено |
| 3.5.5 закрывающий маркер — ближайший с непробельным символом перед ним | `parse_slice` | `rule5_*`, `decision_a2_*` | решение |
| 3.5.6 незакрытое — литералы | `parse_slice` | `rule6_*`, `decision_a3_empty_content_is_literal` | выполнено |
| 3.5.7 code — как есть | `parse_slice` | `rule7_*` | выполнено |
| 3.5.8 ссылка `[текст](url)` | `parser.cpp`: `parse_link` | `rule8_*`, `decision_a5_*` | выполнено |
| 3.5.9 экранирование | `parse_slice`, `escape.cpp` | `rule9_*`, `decision_a7_*`, `test_escape`: `round_trip_*` | выполнено |
| 3.5.10 вложенность, тот же стиль внутри — текст | `parse_slice` (`StyleSet opened`) | `rule10_*`, `decision_a4_*` | выполнено |
| 3.5.11 пустой вход | `parse` | `rule11_empty_input` | выполнено |
| 3.5.12 сортировка диапазонов, нет частичных пересечений | `parser.cpp`: `sort_ranges` | `rule12_*`, `decision_a8_*`, `test_parser_internal`: `sort_ranges_*`, `test_parser_props` | выполнено |
| 3.6 вывод `{"text":…, "ranges":[…]}`, ровно 4 поля диапазона | `json.cpp`: `to_json` | `test_json`: `to_json_*` (в т. ч. `to_json_has_exactly_the_four_range_fields`, `to_json_is_valid_json_for_many_inputs`) | выполнено |
| 3.6 коды возврата 0 / 2 + `{"error": "invalid_utf8"}` | `cli.cpp`: `run`, `json.cpp`: `error_json` | `test_cli`: `run_parse_mode_invalid_utf8_gives_error_and_code_2`; `test_cli_process`: `process_invalid_utf8_in_both_modes`; `test_json`: `error_json_format` | выполнено (после аудита: вывод ровно как в ТЗ, с пробелом после `:`) |
| 3.6 `msgfmt escape`, пример `2 * 3` → `2 \* 3` | `cli.cpp`: `run`, `escape.cpp`: `escape_utf8` | `test_cli`: `run_escape_mode*`; `test_cli_process`: `process_escape` | выполнено |
| 3.6 детерминированность | — | `test_cli`: `run_is_deterministic`, `test_fuzz` (seed фиксирован) | выполнено |
| 3.7 README: сборка, запуск, пример, решения, ограничения | `msgfmt/README.md` | — | выполнено |
| Приложение А, примеры 1–15 | `parse` | `test_parser`: `appendix_a_examples_1_to_8`, `appendix_a_examples_9_to_15`; проверено и вручную через `build/msgfmt` | выполнено |
| Приложение А, пример 9 («по правилам») | `parse` | `decision_a1_example9_both_variants`: `**жирный _и_ курсив**` → italic(7,1); `**жирный _и курсив_**` → italic(7,8) | решение |

## Часть 2. ghreport

| Пункт ТЗ | Где реализовано | Тесты | Статус |
|---|---|---|---|
| 4.2 синтаксис, параметры и значения по умолчанию | `args/args.go`: `Parse`, `ParseRepos`, `parseLimit`, `parseSince` | `args`: `TestParseTable`, `TestParseReposTable`, `TestParseLimit`, `TestParseSince`; `Py` `test_06_errors::test_invalid_arguments_exit_with_code_2` | выполнено |
| 4.2 `--repos`: дубликаты удаляются, первое вхождение остаётся | `args.ParseRepos` | `TestParseReposTable`; `Py` `test_07_report_schema::test_repository_order_follows_the_argument_and_duplicates_are_dropped` | решение (что считать дубликатом) |
| 4.2 `--limit` ≥ 1, `--since` YYYY-MM-DD (00:00 UTC) | `parseLimit`, `parseSince` | `TestParseLimit`, `TestParseSince`, `TestParseSinceIsMidnightUTC` | решение (строгий разбор) |
| 4.2 некорректные аргументы: stderr, пустой stdout, код 2 | `app.Run` | `app`: `TestArgumentErrorsProduceNoStdoutAndNoFiles`; `Py` `test_invalid_arguments_exit_with_code_2` | выполнено |
| 4.3 `GITHUB_TOKEN` (необязателен), `GITHUB_API_BASE_URL` (по умолчанию `https://api.github.com`, слеш в конце допустим) | `config/config.go`: `FromEnv`, `parseBaseURL`, `JoinURL` | `config`: `TestFromEnvBaseURLTable`, `TestFromEnvToken`, `TestJoinURLWithTrailingSlashBase`; `app`: `TestBaseURLVariants`, `TestBaseURLWithTrailingSlashAgainstServer` | выполнено |
| 4.3/4.8 заданный `GITHUB_API_BASE_URL` соблюдается и в контейнере | `Dockerfile` не задаёт `ENV`; `docker run -e GITHUB_API_BASE_URL=…` | `verify_all` шаги `docker-image`/`docker-run`; `tools/tests/test_ghreport_docker.py` (имена переменных передаются обёрткой). Полный прогон из контейнера против stub возможен только на Linux-хосте (`--network host`); на Docker Desktop (Windows) шаг `docker-run` пропускается | выполнено (на Linux-хосте), ограничение на Docker Desktop |
| 4.4.1 метод, путь, параметры `state=open&sort=created&direction=asc&per_page=100` | `issues/issues.go`: `FirstURL` | `issues`: `TestFirstURL`; `Py` `test_08_trace::test_first_url_carries_the_specified_query` | выполнено |
| 4.4.1 заголовки `Accept`, `User-Agent`, `X-GitHub-Api-Version: 2026-03-10`, `Authorization: Bearer` только при токене | `httpx/httpx.go`: `setHeaders` | `httpx`: `TestGetSendsAllHeaders`, `TestGetOmitsOptionalHeaders`; `Py` `test_09_request_format::test_required_request_headers`, `test_authorization_bearer_only_when_a_token_is_set` | выполнено |
| 4.4.1 User-Agent описан в README | `service/README.md`, раздел «User-Agent» | — | выполнено |
| 4.4.1 перенаправления 301, 302, 307 | `httpx.go`: `isFollowedRedirect`, `resolveRedirect` | `httpx`: `TestGetFollowsRedirectCodes`, `TestGetRedirectLimit`, `TestGetBadRedirectsAndOtherStatusesFail`; `Py` `test_redirects_are_followed_by_location[301/302/307]` | выполнено |
| 4.4.1 таймаут запроса ≤ 30 с | `httpx.go`: `timeout` (30 с по умолчанию) | `httpx`: `TestClientTimeout`, `TestGetTimeoutIsTracedAndRetried` | выполнено |
| 4.4 все запросы последовательные | `app.collect`, `issues.FetchOpenIssues` — циклы без горутин (в `service/` нет `go func`) | `app`: `TestRequestsAreSequential`; `Py` `test_requests_are_strictly_sequential` (пересечение запросов по времени на прокси); мутант «параллельность» не вводится (см. api_tests/README) | выполнено |
| 4.4.2 следующая страница — из `Link rel="next"`, адреса вручную не строятся | `linkhdr/linkhdr.go`: `NextURL` | `linkhdr`: `TestNextURLTable`; `issues`: `TestFetchSeveralPages`, `TestFetchRelativeLinks`; `Py` `test_service_requests_exactly_the_page_addresses_it_was_given` (непрозрачный cursor в `Link`); мутант `pages_built_by_hand` убит | выполнено |
| 4.4.2 порядок: страница → отбор → остановка по `limit` или без `next` | `issues.FetchOpenIssues` | `issues`: `TestFetchLimitStopsRequestingPages`, `TestFetchLimitBelowOne`; `Py` `test_01_pagination_limit` | выполнено |
| 4.4.2 порядок элементов: `created_at`, затем `number` | `issues.sortIssues` | `TestSortIssues`, `TestFetchSortsResult`; `Py` `test_sorting_uses_created_at_before_number`, `test_equal_created_at_is_ordered_by_number` | выполнено |
| 4.4.3 исключение PR по ключу `pull_request` | `issues.parseElement` | `TestFetchSkipsPullRequests`, `TestParseElement`; `Py` `test_02_pr_filter` (4 теста, в т. ч. `pull_request: null`) | выполнено |
| 4.4.3 `--since`: сравнение с 00:00:00 UTC, параметр `since` API не используется | `issues.parseElement` (фильтр в службе); в запросе только 4 параметра | `TestFetchSinceBoundaries`, `TestFetchSinceFilteredIssuesDoNotConsumeLimit`; `Py` `test_the_since_parameter_is_not_sent_to_the_api`, `test_since_keeps_an_issue_created_exactly_at_midnight`; мутанты `since_*` убиты | выполнено |
| 4.4.3 дедупликация по `number` | `issues.FetchOpenIssues` | `TestFetchDeduplicatesAcrossPages`, `TestFetchDuplicateKeepsFirstOccurrence`; `Py` `test_duplicate_at_the_page_boundary_is_counted_once`, `test_duplicate_does_not_consume_the_limit`; мутант `dedup_disabled` убит | выполнено |
| 4.4.4 кэш: ETag + тело по адресу, `If-None-Match`, 304 → тело из кэша, `from_cache: true`; 200 → обновление; переживает перезапуск; без токена | `cache/cache.go`: `Key`, `Get`, `Put`; `issues.page` | `cache`: все; `issues`: `TestFetchCacheSecondRunUsesConditionalRequests`, `TestFetchCacheLinkIsStoredAndUsedOn304`, `TestFetchCorruptCacheFileIsMiss`; `app`: `TestSecondRunUsesCache`; `Py` `test_04_cache` (7 тестов, в т. ч. `test_the_token_is_never_written_to_the_cache`) | выполнено |
| 4.4.5 признаки лимита: 403/429 + (тело / `retry-after` / `x-ratelimit-remaining: 0`) | `httpx/ratelimit.go`: `IsRateLimited` | `TestIsRateLimitedTable`; `Py` `test_403_limit_signals_are_waited_out`, `test_plain_403_is_not_a_rate_limit` | выполнено |
| 4.4.5 пауза: `retry-after` → `x-ratelimit-reset` → ≥ 60 с | `ratelimit.go`: `RateLimitWait`, `parseRetryAfter`, `DefaultRateLimitWait` | `TestRateLimitWaitTable`, `TestParseRetryAfter`, `httpx`: `TestGetRateLimitRetryAfter`, `TestGetRateLimitResetHeader`, `TestGetRateLimitBodyOnlyWaitsDefault` (поддельные часы, без реального ожидания); `Py` `test_429_with_retry_after_is_waited_out` | выполнено |
| 4.4.5 сеть/5xx: 3 повтора с задержками 1, 2, 4 с; затем ошибка репозитория, переход к следующему | `httpx.go`: `Get`, `decide`, `retryFailure` | `TestGetServerErrorRetriesWithBackoff`, `TestGetSucceedsOnThirdAttempt`, `TestGetAttemptCountsBothRetryKinds`; `app`: `TestServerErrorsExhaustedGiveRequestFailed`; `Py` `test_persistent_failures_become_request_failed_after_three_retries`; мутант `retries_reduced` убит | выполнено |
| 4.4.6 коды `not_found`, `rate_limited`, `request_failed`, `invalid_response` | `issues.ErrorCode` | `TestErrorCode`, `TestPageErrorsAndStatuses`, `app`: `TestInvalidResponseAndRateLimitedCodes`; `Py` `test_missing_repository_among_existing_ones`, `test_invalid_response_bodies` | выполнено |
| 4.4.6 коды возврата 0/1/2/3 | `exitcode.Summarize`, `app.Run`, `app.finish` | `exitcode`: тесты; `app`: `TestAllRepositoriesFailedIsFatal`, `TestUnauthorizedStopsImmediately`; `Py` `test_401_is_fatal_with_exit_code_3`, `test_all_repositories_missing_is_fatal` | решение (единственный репозиторий с ошибкой → `all_failed`) |
| 4.4.6 фатальные JSON `{"error": "unauthorized"}` / `{"error": "all_failed"}` ровно как в таблице | `model.FatalJSON` | `model`: `TestFatalJSON`; `app`: `TestUnauthorizedStopsImmediately`, `TestAllRepositoriesFailedIsFatal` (сравнение побайтно) | выполнено (расхождение найдено аудитом: был компактный вид без пробела; исправлено коммитом `fix:`) |
| 4.4.6/4.2 код 2 — вывод только в stderr, stdout пуст | `app.Run` | `TestArgumentErrorsProduceNoStdoutAndNoFiles`, `TestEnvironmentAndTraceErrorsExitWithTwo` | выполнено |
| 4.5 схема отчёта, соответствие полей API, порядок репозиториев, `count`, `error: null` | `model/model.go`: `RenderReport`, `Normalize`; `issues.parseElement` | `model`: `TestRenderReportSpecExample` (пример Б.1), `TestNormalizeNilSlices`; `issues`: `TestFetchConvertsFields`; `Py` `test_07_report_schema` (10 тестов) | выполнено |
| 4.5 `generated_at` — UTC RFC 3339 | `model.FormatTimestamp` | `TestFormatTimestamp`; `Py` `test_top_level_keys_and_generated_at` | выполнено |
| 4.6 журнал JSONL: поля `ts, method, url, status, attempt, from_cache, error`; без `Authorization` | `trace/trace.go`: `New`, `Write`; `httpx.log` | `trace`: `TestWriteFormat`, `TestWriteStripsUserinfoAndHasNoAuthorization`; `httpx`: `TestGetTokenNeverInTraceOrErrors`; `Py` `test_08_trace` (6 тестов) | выполнено |
| 4.7 в stdout только отчёт или фатальный JSON; диагностика в stderr | `app.Run`, `scrubWriter` | `app`: `TestOutFileAndStdoutModes`, `TestTraceFileOnlyWhenRequested` | выполнено |
| 4.7 токен не в stdout/stderr/журнале (и не в кэше) | `app.Scrub`, `httpx.redact`, `cache.Put` (токен не передаётся) | `app`: `TestTokenNeverLeaks`; `httpx`: `TestRedact`, `TestGetTokenNeverInTraceOrErrors`; `Py` `test_the_token_never_appears_in_any_output`, `test_no_credentials_in_the_trace`, `test_the_token_is_never_written_to_the_cache`; мутанты `token_printed_to_stderr`, `authorization_in_trace` убиты | выполнено |
| 4.7 (доп.) токен не уходит на другой origin при редиректе | `httpx.sameOrigin` | `TestGetRedirectToOtherOriginDropsToken`; `Py` `test_authorization_is_not_sent_to_another_origin_after_a_redirect` | выполнено |
| 4.8 `service/Dockerfile`, `.dockerignore`; команды `docker build -t ghreport service/` и `docker run --rm -e GITHUB_TOKEN=… ghreport --repos cli/cli --limit 20 --out -` | `service/Dockerfile`, `service/.dockerignore` | `verify_all`: `docker-image` (сборка) | выполнено |
| 4.8 непривилегированный пользователь (`USER`), секреты не в образе, независимость от файлов хоста | `Dockerfile`: `USER 65532:65532`, нет `ENV`/`ARG` с секретами | `verify_all`: `docker-image` проверяет `Config.User` ≠ root и код 2 при `--repos` без значения; `docker history ghreport` не содержит `GITHUB`/токенов (проверено вручную); `Config.Env` содержит только `PATH` и `SSL_CERT_FILE` | выполнено |
| 4.9 `gofmt -l` пуст, `go vet ./...`, `go test ./...` | — | `verify_all`: шаг `service` (`go test ./... -race -count=1`) | выполнено |
| 4.9 нет SDK и сторонних модулей | `service/go.mod` — только `module ghreport` и `go 1.22`, `go.sum` отсутствует; импорты — stdlib и `ghreport/internal/...` | `go list -m all` → один модуль; шаг `service` | выполнено |
| 4.9 README: запуск, архитектура, формат кэша, лимиты, примеры, переменные окружения | `service/README.md` | — | выполнено |
| Приложение Б: примеры отчёта, журнала, работы кэша, ошибок | — | `app`: `TestSpecExampleB1`; `model`: `TestRenderReportSpecExample`; `Py` `test_04_cache` | выполнено |

## Часть 3. api_tests

| Пункт ТЗ | Где реализовано | Тесты / проверка | Статус |
|---|---|---|---|
| 5.1 чёрный ящик: служба запускается отдельным процессом, проверяются отчёт, журнал, коды | `helpers/runner.py`: `run_ghreport`, `run_raw` | `selfcheck/test_runner_selfcheck.py` | выполнено |
| 5.1 работа с любым совместимым сервером (данные неизвестны) | оракул `helpers/oracle.py`, классификатор `helpers/classify.py`, `need(...)` | `selfcheck/test_oracle_selfcheck.py`; прогон против stub с разными флагами | выполнено |
| 5.1 нет сети вне `GITHUB_API_BASE_URL`; только чтение | `helpers/env.py`, прокси и оракул шлют только GET на базовый адрес | `selfcheck/test_env_selfcheck.py` | выполнено |
| 5.1 у каждого теста свой каталог кэша | фикстура `cache_dir` = `tmp_path / "cache"` (conftest.py) | `test_04_cache::test_a_different_cache_directory_starts_cold` | выполнено |
| 5.2 переменные `GHREPORT_BIN`, `GITHUB_API_BASE_URL`, `GITHUB_TOKEN` (необязателен) | `helpers/env.py`: `load_env` | `test_env_selfcheck::test_missing_environment_fails_with_a_clear_message_and_no_traceback` | выполнено |
| 5.3.1 страницы и `--limit`, нет повторов | `test_01_pagination_limit.py` | 7 тестов (см. README: карта) | выполнено |
| 5.3.2 фильтр PR | `test_02_pr_filter.py` | 4 теста | выполнено |
| 5.3.3 `--since` | `test_03_since.py` | 7 тестов | выполнено |
| 5.3.4 кэш: повторный запуск → `from_cache: true`, тот же отчёт | `test_04_cache.py` | `test_second_run_revalidates_every_request_with_the_stored_etag` и ещё 6 | выполнено |
| 5.3.5 429 + `retry-after` → пауза и успех | `test_05_rate_limit.py` | `test_429_with_retry_after_is_waited_out` (+ 403-варианты) | выполнено |
| 5.3.6 401 → код 3; 404 по одному из репозиториев → код 1 и `error` | `test_06_errors.py` | `test_401_is_fatal_with_exit_code_3`, `test_missing_repository_among_existing_ones` | выполнено |
| 5.3.7 схема отчёта, сортировка | `test_07_report_schema.py` | 10 тестов | выполнено |
| 5.3.8 журнал: поля, нет `Authorization` | `test_08_trace.py` | 6 тестов | выполнено |
| 5.4 детерминированность: нет `sleep`, ожидание ограничено | во всём `api_tests/` нет `sleep`; каждый `subprocess` с `timeout` (`runner._execute`, `run_mutants`) | `verify_all`/`grep`: ноль вхождений `time.sleep`/`time.Sleep`; `test_runner_selfcheck::test_run_ghreport_kills_hanging_process_on_timeout` | выполнено |
| 5.4 независимость тестов | у каждого теста свой кэш, свои экземпляры прокси | файлы набора прогнаны в обратном порядке: 83 passed, как и в прямом | выполнено |
| 5.4 прогон ≤ 10 минут | три теста с реальными паузами (`slow`) | основной набор ≈ 30 с (`verify_all`: шаг `suite`, 83 теста); с мутантами (отдельно, `--with-mutants`) ≈ 15 мин | выполнено |
| 5.4 набор обнаруживает нарушения контракта (6 примеров ТЗ) | пробы `helpers/probes.py`, `test_00_server_contract.py` | мутанты сервера: пропуск элемента, PR без признака, 304 без `If-None-Match`, битый JSON, обратная сортировка, нет `Link` — все убиты | выполнено |
| 5.4 таблица убитых мутантов | `api_tests/README.md`, раздел «Мутационная проверка» | `run_mutants.py`: 44 прогона, 43 убито, 1 совместимое отклонение (`dup_items`) остаётся зелёным; код 0 | выполнено |
| 5.5 `conftest.py` с функцией запуска, тесты не дублируют работу с процессами | `conftest.py`: фикстура `ghreport` | все `test_0*.py` | выполнено |
| 5.6 `requirements.txt` (pytest), README с командой `pytest api_tests -q` и переменными | `api_tests/requirements.txt`, `api_tests/README.md` | — | выполнено |

## Что нашёл аудит

| № | Расхождение | Тест, воспроизводящий его | Исправление |
|---|---|---|---|
| 1 | Фатальные JSON службы печатались компактно (`{"error":"all_failed"}`), а ТЗ в таблице 4.4.6 и примерах Б.4 пишет `{"error": "all_failed"}`; аналогично `{"error": "invalid_utf8"}` в 3.6. Любой потребитель, парсящий JSON, не заметит разницы, но побайтное сравнение с ТЗ — заметит | обновлены ожидания в `model_test.go::TestFatalJSON`, `app_e2e_test.go`, `app_internal_test.go`, `test_json.cpp::error_json_format`, `test_cli.cpp`, `test_cli_process.cpp`, `test_fuzz.cpp` (коммит `test:` — тесты падали) | коммит `fix:` — `model.FatalJSON` и `msgfmt::error_json` |
| 2 | Не было проверки чистоты репозитория (секреты, бинарники в истории, `.gitignore`) | `tools/tests/test_check_repo.py` | `tools/check_repo.py`, шаг `hygiene` в `verify_all` |
| 3 | Два коммита начальной истории (`add spec`, `add spec pt. 2`) не в стиле Conventional Commits | — | не исправлялось: историю переписывать запрещено (CLAUDE.md §5); отмечено `check_repo.py` как note |

## Известные ограничения (проверить человеку)

* `docker-run` (полный прогон `ghreport` из контейнера против stub и `pytest` через `tools/ghreport-docker.sh`)
  требует Linux-хоста с нативным Docker: `--network host` на Docker Desktop (Windows/macOS) не даёт контейнеру доступа
  к loopback хоста. В этой среде проверены: сборка образа, `USER 65532`, код 2 для `--repos` без значения,
  монтирование каталогов `--cache-dir/--trace/--out` и uid:gid обёрткой (реальный Docker, недостижимый сервер),
  а также аргументы обёртки (тест с поддельным `docker`). Полный прогон выполнить на Linux: `python tools/verify_all.py`.
* Санитайзеры собираются только там, где компилятор их поддерживает (Linux/WSL); на Windows с mingw шаг пропускается.
* Мутационный прогон (`--with-mutants`) занимает ≈ 15 минут и в обычный прогон не входит.
