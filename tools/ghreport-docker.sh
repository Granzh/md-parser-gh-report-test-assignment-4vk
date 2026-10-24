#!/bin/sh
# Run the ghreport container as if it were the native binary (GHREPORT_BIN for api_tests).
#
# Arguments are forwarded unchanged. The directories named by --cache-dir, --trace and --out (a file other than "-")
# are created on the host and mounted into the container at the SAME absolute path, so paths in arguments stay valid
# on both sides. GITHUB_TOKEN and GITHUB_API_BASE_URL are passed by name only (the values never appear in the command
# line); the container shares the host network (--network host) so that the loopback stub server is reachable, and
# runs as the caller's uid:gid. The image name comes from GHREPORT_IMAGE (default: ghreport).
set -eu

mounts=""
want=""

# add_mount PATH KIND: remember the directory to mount (KIND "dir" = PATH itself, "file" = its parent).
add_mount() {
    case "$1" in /*) path=$1 ;; *) path=$PWD/$1 ;; esac
    if [ "$2" = file ]; then path=$(dirname "$path"); fi
    mkdir -p "$path"
    case " $mounts " in *" $path "*) return ;; esac
    mounts="$mounts $path"
}

for arg in "$@"; do
    if [ -n "$want" ]; then
        if [ "$want" = out ] && [ "$arg" = "-" ]; then want=""; continue; fi
        if [ "$want" = cache ]; then add_mount "$arg" dir; else add_mount "$arg" file; fi
        want=""
        continue
    fi
    case "$arg" in
        --cache-dir) want=cache ;;
        --trace) want=trace ;;
        --out) want=out ;;
        --cache-dir=*) add_mount "${arg#*=}" dir ;;
        --trace=*) add_mount "${arg#*=}" file ;;
        --out=*) if [ "${arg#*=}" != "-" ]; then add_mount "${arg#*=}" file; fi ;;
    esac
done

volumes=""
for dir in $mounts; do
    volumes="$volumes -v $dir:$dir"
done

# shellcheck disable=SC2086  # $volumes is a list of "-v src:dst" words without spaces in paths
exec docker run --rm -i --network host --user "$(id -u):$(id -g)" -w /tmp \
    -e GITHUB_TOKEN -e GITHUB_API_BASE_URL $volumes "${GHREPORT_IMAGE:-ghreport}" "$@"
