# vk-test

[![CI](https://github.com/Granzh/md-parser-gh-report-test-assignment-4vk/actions/workflows/ci.yml/badge.svg)](https://github.com/Granzh/md-parser-gh-report-test-assignment-4vk/actions/workflows/ci.yml)

Учебное задание из трёх частей:

1. **msgfmt** — библиотека и утилита на C++17 (только стандартная библиотека): разбор подмножества Markdown в пару
   «чистый текст + диапазоны форматирования» с позициями в кодовых единицах UTF-16.
2. **ghreport** — служба на Go (только `net/http` и stdlib, без SDK GitHub) в Docker: сводка по открытым issue
   репозиториев GitHub (постраничная выдача по `Link`, кэш ETag, лимиты запросов, повторные попытки, журнал).
3. **api_tests** — интеграционные тесты службы на Python/pytest: чёрный ящик против любого сервера, совместимого с
   контрактом; набор проверяется мутациями (убиваются 43 из 43 нарушений).

Правила разработки — в [CLAUDE.md](CLAUDE.md). Соответствие ТЗ по пунктам — в [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `msgfmt/` | Часть 1: `include/msgfmt`, `src`, `tests`, `CMakeLists.txt`, [README](msgfmt/README.md) |
| `service/` | Часть 2: модуль Go (`cmd/ghreport`, `internal/...`), `Dockerfile`, `.dockerignore`, [README](service/README.md) |
| `api_tests/` | Часть 3: тесты `test_00…test_09`, `helpers/`, `selfcheck/` (stub-сервер, мутанты, мета-тесты), [README](api_tests/README.md) |
| `tools/` | `check_env.py`, `check_contracts.py` (контракты и `covers:`), `check_repo.py` и `check_repo_hygiene.py` (гигиена), `check_workflows.py` (линтер workflow), `verify_all.py`, `ghreport-docker.sh`; тесты в `tools/tests/` |
| `.github/` | `workflows/ci.yml`, `workflows/mutants.yml`, `workflows/release.yml`, `dependabot.yml` — см. раздел «CI/CD» |
| `docs/` | Спецификация (`spec_*.md`), [decisions.md](docs/decisions.md), [COMPLIANCE.md](docs/COMPLIANCE.md), [WORKLOG.md](docs/WORKLOG.md) |

## Быстрый старт

Проверить окружение: `python tools/check_env.py` (ожидается Linux, C++17-компилятор, CMake ≥ 3.20, Go ≥ 1.22,
Python ≥ 3.10, pytest ≥ 7, Docker ≥ 24, git).

### Часть 1 — msgfmt

```
cmake -S msgfmt -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build
printf 'Привет, **мир**!' | build/msgfmt
printf '2 * 3' | build/msgfmt escape
```

С предупреждениями-как-ошибками и санитайзерами (нужен g++/clang с ASan/UBSan, например Linux):
`-DMSGFMT_WERROR=ON -DMSGFMT_SANITIZE=ON`.

### Часть 2 — ghreport

```
docker build -t ghreport service/
docker run --rm -e GITHUB_TOKEN=... ghreport --repos cli/cli --limit 20 --out -
```

Другой сервер, совместимый с контрактом, — тем же способом:
`docker run --rm -e GITHUB_TOKEN=... -e GITHUB_API_BASE_URL=<адрес сервера> ghreport --repos cli/cli --out -`.
Без Docker: `cd service && go build -o ghreport ./cmd/ghreport && GITHUB_TOKEN=... ./ghreport --repos cli/cli --limit 20 --out -`.
Юнит-тесты: `cd service && gofmt -l . && go vet ./... && go test ./... -race -count=1`.

### Часть 3 — api_tests

```
pip install -r api_tests/requirements.txt
python api_tests/selfcheck/stub_server.py --port 8765          # учебный сервер (в другом терминале)
export GHREPORT_BIN=/абсолютный/путь/к/ghreport
export GITHUB_API_BASE_URL=http://127.0.0.1:8765
pytest api_tests -q
```

Мета-тесты самого набора (сервер и бинарник не нужны): `pytest api_tests -m selfcheck -q`.
Мутационная проверка: `python api_tests/selfcheck/run_mutants.py` (≈ 15 минут).

## Проверка всего сразу: `verify_all`

```
python tools/verify_all.py                  # все шаги
python tools/verify_all.py --with-mutants   # + мутационная проверка набора (≈ 15 минут)
python tools/verify_all.py --only service,suite --keep
```

Скрипт делает `git clone --no-hardlinks` во временный каталог (проверяется **только закоммиченное** состояние) и
выполняет шаги `env`, `msgfmt-release` (Release + `-Werror` + ctest), `msgfmt-sanitize` (ASan/UBSan, ноль сообщений),
`service` (gofmt, vet, `go test -race`, сборка), `contracts` (линтер контрактов для msgfmt, service, api_tests, tools), `workflows` (`check_workflows.py`),
`hygiene` (секреты, бинарники, `.gitignore`), `selfchecks`, `suite` (основной набор против stub-сервера и бинарника,
`GHREPORT_STRICT=1`), `docker-image` (сборка, `USER` не root, код 2 при `--repos` без значения), `docker-run` (прогон
из контейнера и `pytest` через [tools/ghreport-docker.sh](tools/ghreport-docker.sh)) и, по флагу, `mutants`. В конце
печатается таблица ok/fail/skip с временем. Код возврата 1, если хотя бы один шаг упал; `skip` всегда с причиной.

Полный прогон, включая санитайзеры и контейнер, рассчитан на Linux-хост с Docker. На Windows санитайзеры (mingw) и
`docker-run` (`--network host` не достаёт до loopback хоста) пропускаются автоматически.

## CI/CD

Конфигурация — в `.github/`. Секреты в CI не нужны: тесты идут против stub-сервера, реальный GitHub API не вызывается.
Права `GITHUB_TOKEN` по умолчанию — только чтение; расширены они лишь у job `release` в `release.yml`.
Все сторонние actions закреплены полным SHA коммита (рядом комментарий с версией); обновления присылает Dependabot.

### Что проверяется (`ci.yml`: push в `main`, pull request, ручной запуск)

| Job | Что делает |
|-----|------------|
| `msgfmt` | матрица gcc/clang × release/sanitize: CMake ≥ 3.20, `-DMSGFMT_WERROR=ON`, `ctest`; ASan/UBSan с `halt_on_error=1`; при падении — артефакт `LastTest.log` |
| `service` | Go из `service/go.mod` (≥ 1.22), запрет зависимостей (нет `require`, пустой `go.sum`), `gofmt -l`, `go vet`, `go test -race`, сборка; бинарник — артефакт на 1 день |
| `docker` | сборка образа; `USER` не root; `--repos` без значения → код 2 и пустой stdout; прогон контейнера против stub (по журналу `--trace` служба обращалась именно по заданному адресу); токена нет в `docker history` и `ENV` |
| `api-tests` | Python 3.10 и 3.12: `pytest api_tests` против stub и бинарника (`GHREPORT_STRICT=1`), затем `-m selfcheck`; на 3.12 ещё и набор через контейнер (`tools/ghreport-docker.sh`); при падении — артефакт с логами stub и pytest |
| `tools-and-lint` | `pytest tools/tests`; линтер контрактов с `--check-covers` для msgfmt, service, api_tests, tools |
| `hygiene` | `tools/check_repo_hygiene.py --history`: секреты, бинарники, файлы > 1 MiB, `.gitignore` — по дереву и всей истории |
| `workflows-lint` | `tools/check_workflows.py` и `actionlint` (официальный образ, закреплён по дайджесту) |
| `ci-ok` | итог: падает, если любой job не `success`. **Только он указывается как required status check** |

Раз в неделю и вручную работает `mutants.yml` (мутационная проверка набора, ≈ 15 минут; таблица — в Summary запуска и
в артефакте `mutants-table`).

### Как воспроизвести локально

```
python tools/verify_all.py                   # все шаги на чистом клоне (нужны закоммиченные изменения)
python tools/check_workflows.py .github/workflows
python tools/check_repo_hygiene.py --history
python tools/check_contracts.py tools --tests tools/tests --check-covers
pytest tools/tests -q
cmake -S msgfmt -B build -DCMAKE_BUILD_TYPE=Release -DMSGFMT_WERROR=ON [-DMSGFMT_SANITIZE=ON] && cmake --build build && ctest --test-dir build --output-on-failure
cd service && gofmt -l . && go vet ./... && go test ./... -race -count=1 && go build -o ghreport ./cmd/ghreport
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12   # синтаксис workflow (нужен Docker)
```

### Форк

На форке workflows по умолчанию отключены: вкладка Actions → «I understand my workflows, go ahead and enable them».
Секреты и переменные настраивать не нужно. Pull request из форка выполняется с правами только на чтение.

### Релиз

Тег `vX.Y.Z` на коммите, прошедшем CI, запускает `release.yml`: тесты, сборка Linux-бинарника, образ
`ghcr.io/<owner>/<repo>:vX.Y.Z` (и `latest`) и **черновик** GitHub Release с бинарником и `SHA256SUMS`. Публикует Release
человек. Тег создаётся вручную (`git tag vX.Y.Z && git push origin vX.Y.Z`); в этом репозитории теги не создавались.

### Артефакты

Actions → нужный запуск → раздел Artifacts внизу страницы: `msgfmt-<компилятор>-<режим>-logs`,
`api-tests-py<версия>-logs`, `ghreport-linux` (хранится 1 день), `mutants-table`. Таблица мутантов также в Summary запуска `Mutants`.

### Ручные шаги (вне файлов репозитория)

1. Включить GitHub Actions для репозитория.
2. Защита ветки `main`: required status check — только `ci-ok`; запретить force-push и удаление ветки.
3. Settings → Actions → General → Workflow permissions: «Read repository contents permissions» для `GITHUB_TOKEN`.
4. Включить Dependabot alerts и Dependabot version updates.
5. Для GHCR: после первого релиза проверить видимость пакета (Packages → Package settings) и связать его с репозиторием.
6. Дать куратору доступ к репозиторию (Settings → Collaborators) или сделать его публичным.
7. Подставить `<OWNER>/<REPO>` в бейдж этого README.

## Затраченное время

| Этап | Что сделано | Время |
|------|-------------|-------|
| 0. Инфраструктура | структура, `check_env`, `check_contracts` | 20 минут |
| 1. msgfmt | каркас, UTF-8, парсер, escape, JSON, CLI, фаззинг | 3 часа |
| 2. ghreport | пакеты, транспорт, кэш, сборка службы, Docker | 4 часов |
| 3. api_tests | инфраструктура, тесты службы, мутации | 2.5 часа |
| 4. Финальная проверка | `verify_all`, `check_repo`, COMPLIANCE, README | 2 часа |
| **Итого** | | 11 часов 50 минут |

## Использованные ИИ-инструменты

- Claude Code (CLI от Anthropic) — написание кода, тестов и документации по всем этапам.

## Ключевые принятые решения

Полный перечень с обоснованиями и именами тестов — [docs/decisions.md](docs/decisions.md). Главное:

* **Правила ТЗ приоритетнее примеров.** Пример 9: `**жирный _и курсив_**` даёт italic(7,8), а `**жирный _и_ курсив**` —
  italic(7,1); тестируются оба входа.
* **«Буква или цифра»** (правило 3) — переносимая таблица интервалов категорий L* и Nd без `<locale>`/ICU;
  письменности вне списка и комбинирующие знаки не входят.
* **UTF-8 декодер строгий** (overlong, суррогаты, > U+10FFFF — ошибка); коды msgfmt при неверных аргументах: 64, при
  внутренней ошибке: 70 (ТЗ определяет только 0 и 2).
* **Формат JSON:** отчёты компактные; объекты ошибок печатаются ровно как в ТЗ — `{"error": "all_failed"}`.
* **ghreport:** единственный репозиторий с ошибкой — `all_failed` (код 3); `-h` — код 0; строгий разбор `--limit` и
  `--since`; ошибки кэша — предупреждение, служба работает без кэша; токен не уходит на другой origin при редиректе.
* **api_tests:** ожидаемый результат вычисляет оракул (сервер заранее неизвестен); сбои вносит прокси; нехватка данных
  у сервера — `skip` с причиной (в `GHREPORT_STRICT=1` — падение); реальные паузы только в трёх тестах `slow`.
* **Контракты и `covers:`** у каждой функции проверяются `tools/check_contracts.py`.

## Известные ограничения

* Определение «буквы/цифры» приближённое (таблица, а не полная база Unicode), см. [msgfmt/README.md](msgfmt/README.md).
* Санитайзеры на Windows (mingw) недоступны; проверены в Linux (WSL Fedora, g++ 14).
* Полный прогон контейнера против локального сервера (`docker-run`) возможен только на Linux-хосте; на Docker Desktop
  проверены сборка образа, `USER 65532`, код 2, монтирования и аргументы обёртки.
* Набор api_tests не видит того, что не отражается в трафике, выводе и файлах (внутренний формат кэша, реальные
  таймауты 30 с, сигналы); граница `--since` для смещений часовых поясов зависит от данных сервера. Подробнее —
  [api_tests/README.md](api_tests/README.md).
* Первые два коммита истории (`add spec`, `add spec pt. 2`) не в стиле Conventional Commits; историю не переписывали.
* Раздел 6 ТЗ в `docs/spec_*.md` отсутствует и не аудировался.
