'use strict';

// Hosted-only, read-only EF02 outcome observer; the only write is an Issue comment.
// Never modify claims, retry an attempt, or run Codex/Antigravity.
const AGE_MS = 45 * 60 * 1000;
const TARGET_WORKFLOW = 'EF02 Multi-Action remote engineering';
const TARGET_FILE = 'ef02-batch.yml';

function issueNumber(run, repoFullName) {
  if (!run || run.name !== TARGET_WORKFLOW ||
      run.head_repository?.full_name !== repoFullName) return null;
  const match = /^EF02 Issue #(\d+)$/.exec(run.display_title || '');
  if (!match) return null;
  const id = Number(match[1]);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function classifyTerminal(run, jobs) {
  if (run.status !== 'completed' || run.conclusion === 'success') return null;
  const validate = jobs.find(j => j.name === 'validate');
  const execute = jobs.find(j => j.name === 'execute');
  if (validate?.conclusion === 'failure' && execute?.conclusion === 'skipped') {
    return {code: 'HOSTED_INGRESS_REJECTED', detail:
      'Hosted validation/ingress failed and Windows execution was skipped. ' +
      'This workflow did not invoke the Windows executor.'};
  }
  if (run.conclusion === 'cancelled') {
    return {code: 'WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN', detail:
      'GitHub workflow was cancelled. Executor invocation/side effects are UNKNOWN. ' +
      'Reconcile the distributed claim and candidate refs before any new attempt.'};
  }
  if (run.conclusion === 'timed_out') {
    return {code: 'WORKFLOW_TIMED_OUT_EXECUTION_UNCERTAIN', detail:
      'GitHub workflow timed out. Executor invocation/side effects are UNKNOWN. ' +
      'Reconcile the distributed claim and candidate refs before any new attempt.'};
  }
  return {code: 'WORKFLOW_NONPASS_UNRECONCILED', detail:
    'The workflow ended without a verified overall PASS. Consult job logs, ' +
    'batch_result.json and claim/candidate refs; do not infer provider outcome.'};
}

function classifyQueued(run, jobs, nowMs) {
  if (run.status !== 'queued' && run.status !== 'in_progress') return null;
  const age = nowMs - Date.parse(run.created_at || '');
  if (!Number.isFinite(age) || age < AGE_MS) return null;
  const validated = jobs.some(j => j.name === 'validate' && j.conclusion === 'success');
  const queuedExecute = jobs.some(j => j.name === 'execute' && j.status === 'queued');
  if (!validated || !queuedExecute) return null;
  return {code: 'WINDOWS_JOB_WAITING_UNCONFIRMED', detail:
    'Windows execute job has remained QUEUED after hosted validation. ' +
    'Runner offline is NOT established; possible causes include busy runner or capacity. ' +
    'This is not a PASS and does not authorize any retry.'};
}

async function notify({github, context, core}, run, status, issue) {
  const owner = context.repo.owner, repo = context.repo.repo;
  const {data: target} = await github.rest.issues.get({owner, repo, issue_number: issue});
  if (target.pull_request || !target.title?.startsWith('[EF02]')) return false;
  const marker = 'EF02_OBSERVER run=' + run.id + ' code=' + status.code;
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: issue, per_page: 100
  });
  const alreadyReported = comments.some(c => {
    if (c.user?.login !== 'github-actions[bot]') return false;
    const body = c.body || '';
    if (body.includes(marker)) return true;
    // Existing EF02 worker/validation feedback takes precedence for terminal runs.
    return status.code !== 'WINDOWS_JOB_WAITING_UNCONFIRMED' &&
      (body.includes('/actions/runs/' + run.id) ||
       body.includes('GitHub workflow run: ' + run.id));
  });
  if (alreadyReported) {
    core.info('EF02 outcome already reported: ' + run.id);
    return false;
  }
  await github.rest.issues.createComment({
    owner, repo, issue_number: issue,
    body: [marker, 'Status: `' + status.code + '`',
      status.detail,
      'Workflow: ' + (run.html_url ||
        'https://github.com/' + owner + '/' + repo + '/actions/runs/' + run.id),
      'This observer does not run executors, cancel jobs, mutate claims or retry models.']
      .join('\n\n')
  });
  return true;
}

async function observe({github, context, core}, {nowMs = Date.now()} = {}) {
  const repoFullName = context.repo.owner + '/' + context.repo.repo;
  const event = context.eventName;
  if (event === 'workflow_run') {
    const run = context.payload?.workflow_run;
    const issue = issueNumber(run, repoFullName);
    if (!issue || run.status !== 'completed' || run.conclusion === 'success') return 0;
    const {data} = await github.rest.actions.listJobsForWorkflowRun({
      owner: context.repo.owner, repo: context.repo.repo, run_id: run.id, per_page: 100
    });
    const status = classifyTerminal(run, data.jobs || []);
    if (!status) return 0;
    return (await notify({github, context, core}, run, status, issue)) ? 1 : 0;
  }
  if (event !== 'schedule') return 0;
  const {data} = await github.rest.actions.listWorkflowRuns({
    owner: context.repo.owner, repo: context.repo.repo,
    workflow_id: TARGET_FILE, per_page: 100
  });
  let reported = 0;
  for (const run of data.workflow_runs || []) {
    if (run.status !== 'queued' && run.status !== 'in_progress') continue;
    const issue = issueNumber(run, repoFullName);
    if (!issue) continue;
    const age = nowMs - Date.parse(run.created_at || '');
    if (!Number.isFinite(age) || age < AGE_MS) continue;
    const {data: jobs} = await github.rest.actions.listJobsForWorkflowRun({
      owner: context.repo.owner, repo: context.repo.repo, run_id: run.id, per_page: 100
    });
    const status = classifyQueued(run, jobs.jobs || [], nowMs);
    if (status && await notify({github, context, core}, run, status, issue)) reported++;
  }
  return reported;
}

module.exports = Object.assign(observe, {issueNumber, classifyTerminal, classifyQueued});
