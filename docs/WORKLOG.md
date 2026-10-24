# Журнал работ

| дата | этап | что сделано | время | инструменты |
|------|------|-------------|-------|-------------|
| 2026-10-23 | 0. Инфраструктура | Структура репозитория, .gitignore, CLAUDE.md, README, tools/check_env.py и tools/check_contracts.py с тестами | | Claude Code |
| 2026-10-23 | 1.1 msgfmt: каркас, UTF-8, классы символов | CMake (WERROR/SANITIZE, ctest, contracts_lint), contract.hpp, тест-харнесс с самопроверкой, utf8, charclass и тесты | | Claude Code |
| 2026-10-23 | 1.2 msgfmt: ядро парсера | Решения A1-A8 (decisions.md), parser.hpp/parser_internal.hpp/parser.cpp, тесты (примеры А, правила 1-12, detail, перебор строк, инварианты) | | Claude Code |
| 2026-10-23 | 1.3 msgfmt: escape, JSON, CLI | escape, json, cli (run + main), JSON-валидатор для тестов, процессный тест | | Claude Code |
| 2026-10-23 | 1.4 msgfmt: доводка и проверка | Фаззинг и свойства (test_fuzz, seed фиксирован), прогон Debug/RelWithDebInfo/Release с санитайзерами и без, gcov-покрытие, msgfmt/README.md, корневой README | | Claude Code |
| 2026-10-24 | 2.1 ghreport: каркас и базовые пакеты | go.mod, internal/{args,config,model,exitcode,clock,output} с контрактами и таблицами тестов, правка линтера для Go raw-строк | | Claude Code |
| 2026-10-24 | 2.2 ghreport: транспортный слой | linkhdr, trace, httpx (заголовки, ручные редиректы, повторы, лимиты), service/README.md, решения в decisions.md | | Claude Code |
| 2026-10-24 | 2.3 ghreport: кэш и issue репозитория | cache (ETag+Link), testutil.FakeGitHub, issues.Fetcher (пагинация по Link, limit, since, дедупликация, фильтр PR, ErrorCode) | | Claude Code |
| 2026-10-24 | 2.4 ghreport: сборка службы | internal/app (Run, Scrub), cmd/ghreport/main.go, сквозные тесты на FakeGitHub, ручной прогон бинарника против python http.server | | Claude Code |
| 2026-10-24 | 2.5 ghreport: контейнеризация и документация | service/Dockerfile (distroless nonroot), .dockerignore, полный service/README.md, Быстрый старт части 2; образ собран и проверен в Docker 28.3.2 (14 МБ, USER 65532, код 2 без значения, запуск против локального сервера, токена нет в выводе и history) | | Claude Code |
| 2026-10-24 | 3.1 api_tests: инфраструктура | pytest.ini (selfcheck), helpers (runner, оракул, FaultProxy, env/need, классификатор, пробы), conftest, stub_server с мутантами, мета-тесты (-m selfcheck); проверено на собранном ghreport против stub через прокси | | Claude Code |
| 2026-10-24 | 3.2 api_tests: тесты службы | Модули test_00…test_09 (пробы сервера, пагинация/limit, PR, since, кэш, лимиты, ошибки, схема, trace, формат запросов), expect/run_raw/пробы; 78 тестов на Go-службе и stub: 0 падений, 0 skip; мутации службы (null-PR, без дедупликации) и сервера обнаружены | | Claude Code |
| 2026-10-24 | 3.3 api_tests: мутационная проверка | run_mutants.py (6 мутантов сервера + dup_items + 37 мутантов службы), мета-тесты, усиление тестов по итогам прогонов, README части 3 с реальной таблицей; итог 43/43 убито, код 0 | | Claude Code |
| 2026-10-24 | 4. Финальная проверка | tools/verify_all.py (чистый клон, таблица ok/fail/skip), tools/check_repo.py (гигиена), tools/ghreport-docker.sh, аудит ТЗ (docs/COMPLIANCE.md) и исправление формата фатальных JSON, корневой README, шаблон письма | | Claude Code |
