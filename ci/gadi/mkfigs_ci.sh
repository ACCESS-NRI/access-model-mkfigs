#!/bin/bash
# Gadi side of .github/workflows/mkfigs.yml, run on a login node over SSH:
#
#   mkfigs_ci.sh STATE_DIR COMMAND
#
# STATE_DIR (<checkout>.ci) holds ci.env (settings written by the workflow), this script, and what the run
# leaves behind (job id, git plan, git bundle). Commands, in the order the workflow uses them:
#
#   prepare  MODE=new: check out the paper repo at the workflow's commit (SHA, on main).
#            MODE=update, or MODE=preview of a published experiment: check out its eval-<ENAME>
#            branch (or latest tag), take ONLY the listed notebooks from SHA (main for update, any
#            branch for preview; everything else, mkfigs.sh and the pinned access-model-mkfigs
#            included, stays as published), and restore the earlier results (mkfigs.restore).
#            MODE=preview of an unpublished experiment: check out SHA.
#            Then point mkfigs.sh at the experiment (mkfigs.set_experiment), and write run.env
#            (BASE, ESMDIR, MKFIGS_COMMIT) and changes.patch (the edits on top of BASE) for the
#            workflow's preview build
#   submit   qsub the notebooks: one job each with mkfigs.submit if the repo supports it,
#            else one `qsub mkfigs.sh`. Prints jobid=<the job to wait for>
#   status   prints state=<PBS state> exit=<exit status> for that job
#   publish  mkfigs.pushit (Figshare upload + docs pages), then mkfigs.pushit --publish (integrity
#            check, publish), then commit and tag as the git plan says, and bundle the new branch
#            and tag for the workflow to push
#
# The login-environment fallback, qstat parsing and paper-repo fetch/checkout follow access-om3-live's
# gadi/submit.sh (https://github.com/chrisb13/access-om3-live).
#
# publish reads the Figshare token from stdin, so it never appears in a command line (visible
# to other users of the login node) or a file. Nothing else here talks to Figshare.
set -euo pipefail
STATE=$1 CMD=$2   # (MODE, from ci.env, is the run's mode: new, update or preview)
# shellcheck source=/dev/null
source "$STATE/ci.env"   # MODE WORK REPO REF SHA ENAME ESMDIR NOTEBOOKS NB_DIR PROJECT RUN_URL ACTOR ACTOR_ID MKFIGS_SHA PARAMETERS
PARAMETERS=${PARAMETERS:-}   # blank: keep mkfigs.sh's (a new experiment's mkfigs.sh normally has none)
MKFIGS_SHA=${MKFIGS_SHA:-}   # blank: the submodule commit pinned at SHA
ESMDIR=${ESMDIR:-}           # blank (update mode only): the one the published experiment used
MKFIGS_DIR=external/access-model-mkfigs
CHECKOUT=${STATE%.ci}   # the workflow puts STATE next to the checkout: <checkout>.ci

# Non-interactive SSH gets PATH=/bin:/usr/bin; load the login environment for module/qsub.
if ! command -v qsub >/dev/null || ! command -v module >/dev/null; then
    set +eu
    # shellcheck source=/dev/null
    source /etc/profile >/dev/null 2>&1
    set -eu
fi

load_python() {   # the conda module mkfigs.sh itself loads, and the repo's pinned mkfigs
    local lines
    lines=$(grep -E '^[[:space:]]*module[[:space:]]+(purge|use|load)[[:space:]]' "$CHECKOUT/$NB_DIR/mkfigs.sh" || true)
    [ -n "$lines" ] || { echo "no 'module load' lines in mkfigs.sh" >&2; exit 1; }
    set +eu; eval "$lines"; set -eu
    export PYTHONPATH="$CHECKOUT/$MKFIGS_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
}

read_token() {
    IFS= read -r FIGSHARE_TOKEN || true
    [ -n "${FIGSHARE_TOKEN:-}" ] || { echo "no Figshare token on stdin" >&2; exit 1; }
    export FIGSHARE_TOKEN
}

case $CMD in
prepare)
    mkdir -p "$(dirname "$CHECKOUT")"
    [ -d "$CHECKOUT/.git" ] || git clone -q "https://github.com/$REPO.git" "$CHECKOUT"
    cd "$CHECKOUT"
    git fetch -q --force --tags origin "$SHA"
    remote_eval=$(git ls-remote --heads origin "eval-$ENAME" | cut -f1)
    published_tag=$(git tag -l "$ENAME-*" | sort -V | tail -1)
    published=$([ -n "$remote_eval$published_tag" ] && echo yes || echo no)

    case $MODE/$published in
    update/no)
        echo "$ENAME has not been published (no eval-$ENAME branch or $ENAME-* tag): use 'new experiment'." >&2
        exit 1 ;;
    new/yes)
        echo "$ENAME is already published: use 'update a published experiment' (or 'preview a branch')." >&2
        exit 1 ;;
    esac
    if [ "$published" = yes ]; then   # update, or a preview of a published experiment
        if [ -n "$remote_eval" ]; then
            git fetch -q origin "eval-$ENAME"
            BASE=$(git rev-parse FETCH_HEAD); from="eval-$ENAME"
        else   # older experiments may have only a tag
            BASE=$(git rev-parse "$published_tag^{commit}"); from=$published_tag
        fi
    else
        BASE=$SHA; from="$REF"
    fi
    git checkout -q --force --detach "$BASE"
    git submodule -q update --init --recursive --force   # 'pinned': the version BASE records
    echo "Paper repo: $REPO at ${BASE:0:7} ($from), in $CHECKOUT"

    if [ "$published" = yes ]; then
        # The datastore the published experiment used, unless a new one was given.
        if [ -z "$ESMDIR" ]; then
            line=$(grep -E '^[[:space:]]*ESMDIR=' "$NB_DIR/mkfigs.sh" | tail -1)
            ESMDIR=${line#*=}; ESMDIR=${ESMDIR%%[[:space:]#]*}; ESMDIR=${ESMDIR//\"/}
            ESMDIR=${ESMDIR//\$\{ENAME\}/$ENAME}; ESMDIR=${ESMDIR//\$ENAME/$ENAME}
            [[ $ESMDIR =~ ^/(g/data|scratch)/[a-z0-9]+/[A-Za-z0-9._+/-]+\.json$ ]] \
                || { echo "could not read a datastore path from $from's mkfigs.sh (got '$ESMDIR'): give one" >&2; exit 1; }
        fi
        # ONLY the listed notebooks come from SHA; everything else stays as published.
        [ -n "$NOTEBOOKS" ] || { echo "list the notebooks to run" >&2; exit 1; }
        paths=()
        for nb in $NOTEBOOKS; do
            git cat-file -e "$SHA:$NB_DIR/$nb.ipynb" 2>/dev/null \
                || { echo "$NB_DIR/$nb.ipynb is not on $REF" >&2; exit 1; }
            paths+=("$NB_DIR/$nb.ipynb")
        done
        git checkout -q "$SHA" -- "${paths[@]}"
        echo "Notebooks from $REF (${SHA:0:7}): $NOTEBOOKS"
    fi
    if [ -n "$MKFIGS_SHA" ] && [ "$(git -C "$MKFIGS_DIR" rev-parse HEAD)" != "$MKFIGS_SHA" ]; then
        git -C "$MKFIGS_DIR" fetch -q origin "$MKFIGS_SHA"
        git -C "$MKFIGS_DIR" checkout -q --detach "$MKFIGS_SHA"
    fi
    echo "access-model-mkfigs: $(git -C "$MKFIGS_DIR" describe --tags --always)" \
         "($([ -n "$MKFIGS_SHA" ] && echo "chosen in the form" || echo "pinned: as recorded on $from"))"
    echo "Datastore: $ESMDIR"

    [ -r "$ESMDIR" ] || { echo "cannot read the datastore $ESMDIR (as ${USER:-the Gadi user})" >&2; exit 1; }

    load_python
    python3 -c 'import mkfigs.set_experiment, mkfigs.gallery, mkfigs.pushit' 2>/dev/null || {
        echo "This access-model-mkfigs is too old for the CI workflow (no mkfigs.set_experiment" >&2
        echo "or mkfigs.gallery). Bump the submodule, or run with mkfigs_version 'latest release'." >&2; exit 1; }
    cd "$CHECKOUT/$NB_DIR"
    # shellcheck disable=SC2086  # NOTEBOOKS is a validated, space-separated list
    python3 -m mkfigs.set_experiment --ename "$ENAME" --esmdir "$ESMDIR" --note "$RUN_URL" \
        ${NOTEBOOKS:+--notebooks $NOTEBOOKS} ${PARAMETERS:+--parameters $PARAMETERS}
    # The parameters this run uses (given, or kept from mkfigs.sh), for the run summary.
    run_parameters=$(bash -c 'source <(grep -E "^[[:space:]]*(export[[:space:]]+)?MKFIGS_PARAMETERS=" mkfigs.sh | tail -1); echo "${MKFIGS_PARAMETERS:-}"')
    if [ "$published" = yes ]; then
        python3 -m mkfigs.restore --ename "$ENAME"   # the published notebooks, from Figshare
    fi
    cd "$CHECKOUT"
    git --no-pager diff --stat "$BASE" -- . ":(exclude)$MKFIGS_DIR"
    git diff --binary "$BASE" -- . ":(exclude)$MKFIGS_DIR" > "$STATE/changes.patch"
    printf 'MODE=%q\nPUBLISHED=%q\nBASE=%q\nESMDIR=%q\nMKFIGS_COMMIT=%q\nRUN_PARAMETERS=%q\n' "$MODE" "$published" \
        "$BASE" "$ESMDIR" "$(git -C "$MKFIGS_DIR" rev-parse HEAD)" "$run_parameters" > "$STATE/run.env"
    : > "$STATE/plan.json"   # a git plan from an earlier run of this experiment no longer applies
    ;;

submit)
    cd "$CHECKOUT/$NB_DIR"
    load_python
    export PROJECT
    if [ -f "$CHECKOUT/$MKFIGS_DIR/src/mkfigs/submit.py" ] \
       && grep -q 'MKFIGS_NOTEBOOKS:-' mkfigs.sh; then
        # One job per notebook plus a summary job that waits for all of them: wait for that.
        out=$(python3 -m mkfigs.submit)
        echo "$out" >&2
        job=$(sed -n 's/^ *-> *//p' <<< "$out" | tail -1)
    else
        echo "Running all notebooks in one job (qsub mkfigs.sh)" >&2
        job=$(qsub -P "$PROJECT" mkfigs.sh)
    fi
    [ -n "$job" ] || { echo "could not read a job id from qsub" >&2; exit 1; }
    echo "$job" > "$STATE/jobid"
    echo "jobid=$job"
    ;;

status)
    job=$(cat "$STATE/jobid")
    info=$(qstat -xf "$job" 2>/dev/null || true)
    state=$(awk -F' = ' '/job_state/ {print $2}' <<< "$info")
    code=$(awk -F' = ' '/Exit_status/ {print $2}' <<< "$info")
    echo "job=$job state=${state:-unknown} exit=${code:-}"
    ;;

publish)
    read_token
    # shellcheck source=/dev/null
    source "$STATE/run.env"   # BASE: what the commit goes on top of
    cd "$CHECKOUT"
    remote_eval=$(git ls-remote --heads origin "eval-$ENAME" | cut -f1)
    if [ "$MODE" = preview ]; then
        echo "This is a preview run: it cannot publish." >&2; exit 1
    fi
    if [ "$MODE" = new ] && [ -n "$remote_eval" ]; then
        echo "$ENAME was published while this run was going (eval-$ENAME exists). Nothing was published." >&2
        exit 1
    fi
    if [ "$MODE" = update ] && [ -n "$remote_eval" ] && [ "$remote_eval" != "$BASE" ]; then
        echo "eval-$ENAME has moved since this run started (${BASE:0:7} -> ${remote_eval:0:7})." >&2
        echo "Nothing was published. Run the update again." >&2
        exit 1
    fi
    cd "$CHECKOUT/$NB_DIR"
    load_python
    plan="$STATE/plan.json"
    field() { python3 -c 'import json, sys; v = json.load(open(sys.argv[1]))[sys.argv[2]]; print("\n".join(v) if isinstance(v, list) else v)' "$plan" "$1"; }

    # A re-run of this step after this run's commit was made only rebuilds the bundle.
    if [ -s "$plan" ] && git rev-parse -q --verify "refs/tags/$(field tag)" >/dev/null \
       && [ "$(git rev-parse "$(field tag)^{commit}~1")" = "$BASE" ]; then
        echo "Already committed and tagged $(field tag); rebuilding the bundle only."
    else
        python3 -m mkfigs.pushit --ename "$ENAME"   # create/update the private article, write docs pages
        python3 -m mkfigs.pushit --publish --ename "$ENAME" --git-plan "$plan"
        cd "$CHECKOUT"
        branch=$(field branch) tag=$(field tag)
        git switch -q -C "$branch"
        mapfile -t files < <(field files; field recommended_files)
        for f in "${files[@]}"; do
            if [ -e "$f" ] || git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then git add -- "$f"; fi
        done
        # Record the access-model-mkfigs that made the figures, if it isn't the pinned one.
        git add -- "$MKFIGS_DIR"
        mkfigs_version=$(git -C "$MKFIGS_DIR" describe --tags --always)
        who=(-c "user.name=$ACTOR" -c "user.email=$ACTOR_ID+$ACTOR@users.noreply.github.com")
        git "${who[@]}" commit -q -m "$(field commit_message)" \
            -m "Made by $RUN_URL with access-model-mkfigs $mkfigs_version."
        git "${who[@]}" tag -a "$tag" -m "$(field tag_message)"
        git --no-pager show --stat --format='%H %s' HEAD
    fi
    cd "$CHECKOUT"
    git bundle create -q "$STATE/result.bundle" "refs/heads/$(field branch)" "refs/tags/$(field tag)" "^$BASE"
    echo "bundle written: $STATE/result.bundle"
    ;;

*)
    echo "unknown command: $CMD" >&2; exit 2 ;;
esac
