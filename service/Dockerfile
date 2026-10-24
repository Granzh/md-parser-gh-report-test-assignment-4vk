# ghreport: многоэтапная сборка.
#
# Финальный образ — distroless/static-debian12:nonroot, а не alpine: в нём уже есть корневые сертификаты
# (HTTPS к api.github.com работает), нет shell и пакетного менеджера (меньше поверхность атаки), заранее заведён
# непривилегированный пользователь 65532. Бинарник статический (CGO_ENABLED=0), поэтому libc не нужна.
#
# Токен и адрес API НЕ задаются здесь ни через ENV, ни через ARG: они передаются при запуске
# (docker run -e GITHUB_TOKEN=... [-e GITHUB_API_BASE_URL=...]) и не попадают в слои образа.

FROM golang:1.22 AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/ghreport ./cmd/ghreport \
    && mkdir /out/work

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ghreport /ghreport
# Рабочий каталог принадлежит пользователю контейнера: туда пишется кэш по умолчанию (./.ghreport-cache).
COPY --from=build --chown=65532:65532 /out/work /work
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["/ghreport"]
