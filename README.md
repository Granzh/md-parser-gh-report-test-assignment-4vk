# vk-test

Учебное задание из трёх частей: C++-библиотека `msgfmt`, Go-служба `ghreport` (Docker),
Python-интеграционные тесты. Правила разработки — в [CLAUDE.md](CLAUDE.md).

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `msgfmt/` | Часть 1: C++-библиотека (`include/msgfmt`, `src`, `tests`) |
| `service/` | Часть 2: Go-служба `ghreport` и Dockerfile |
| `api_tests/` | Часть 3: Python-интеграционные тесты |
| `docs/` | Спецификация, `decisions.md`, `WORKLOG.md` |
| `tools/` | Проверка окружения и линтер контрактов; тесты в `tools/tests/` |

## Быстрый старт

- Окружение: `python tools/check_env.py`
- Контракты и покрытие: `python tools/check_contracts.py tools --tests tools/tests --check-covers`
- Тесты инструментов: `python -m pytest tools/tests -q`
- Часть 1 (msgfmt), подробности — [msgfmt/README.md](msgfmt/README.md):
  ```
  cmake -S msgfmt -B build -DCMAKE_BUILD_TYPE=Release
  cmake --build build
  ctest --test-dir build
  printf 'Привет, **мир**!' | build/msgfmt
  ```
  С санитайзерами: добавить `-DMSGFMT_SANITIZE=ON -DMSGFMT_WERROR=ON` (нужен g++/clang с поддержкой ASan/UBSan).
- Часть 2 (ghreport), подробности — [service/README.md](service/README.md):
  ```
  cd service && gofmt -l . && go vet ./... && go test ./... -race -count=1
  go build -o ghreport ./cmd/ghreport
  GITHUB_TOKEN=... ./ghreport --repos cli/cli --limit 20 --out -
  docker build -t ghreport service/
  docker run --rm -e GITHUB_TOKEN=... ghreport --repos cli/cli --limit 20 --out -
  ```
  Другой адрес API: `-e GITHUB_API_BASE_URL=<адрес>` (из контейнера до сервера на хосте —
  `--add-host=host.docker.internal:host-gateway` и `http://host.docker.internal:<порт>`).
  Проверка контрактов: `python tools/check_contracts.py service --tests service --check-covers`
- Часть 3 (api_tests): _заглушка_

## Затраченное время

| Этап | Время |
|------|-------|
| 0. Инфраструктура | |
| 1. msgfmt | |
| 2. ghreport | |
| 3. api_tests | |

## Использованные ИИ-инструменты

- Claude Code

## Принятые решения

См. [docs/decisions.md](docs/decisions.md).
