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
