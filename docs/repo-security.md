# Repository security settings

The files in this repository (`.github/dependabot.yml`, the pinned actions in `.github/workflows/ci.yml`, and `SECURITY.md`) are only half of the setup. The other half is a set of switches on GitHub's website. Those switches are not stored in git, and merging a pull request does not flip them.

Sign in as the owner of [sixOfOne/dutch-blitz](https://github.com/sixOfOne/dutch-blitz) and work through the sections below once. Each section says what to click and what that switch protects against.

## 1. Protect `main`

A branch ruleset on `main` stops a direct push. Changes arrive as a pull request, and the pull request cannot merge until the two CI checks have passed. The branch also cannot be deleted or force-pushed.

That protects against a push straight to `main` that skipped the tests, a `git push --force` that throws away history, and someone deleting `main`. The two checks are the job names in `.github/workflows/ci.yml`: **Python tests** and **Terraform and Ansible**. Python tests also runs ruff and mypy. Terraform and Ansible also runs tflint. Those tools are steps inside the two jobs, not extra check names, so this list does not grow when they are added.

Leave admin bypass on. You are the only maintainer. Bypass means you, as repository admin, can still push to `main`, force-push, delete the branch, or merge a pull request while a check is red. That is the escape hatch when a check is broken and the fix has to land, or when you need to revert in a hurry. The cost is that the rule does not apply to you. It also does not stop an attacker who has stolen your admin credentials. A token that can write to the repo but is not an admin still has to open a pull request and pass both checks.

Later, when you want the rule to apply to yourself, remove the repository admin entry from the bypass list. A middle step, if the bypass dialog offers it, is **For pull requests only**: you still have to open a pull request, and you can merge it without waiting for the checks. Leave the mode on **Always allow** for now.

Do not require approving reviews. Required approvals can be set to 0. With zero approvals the pull request is still required, and there is nobody else who would have to approve it. Requiring one approval would leave you as the only person who can approve, which means you could only merge by using the bypass.

### Ruleset

1. Open the repository, then **Settings**.
2. In the left sidebar, under **Code, planning, and automation**, click **Rules**, then **Rulesets**.
3. Click **New ruleset**, then **New branch ruleset**.
4. Ruleset name: `protect main`.
5. Enforcement status: **Active**.
6. Under **Bypass list**, click **Add bypass**. Choose the **Repository admin** role and add it. Leave the bypass mode as **Always allow**.
7. Under **Target branches**, click **Add target**, then **Include default branch**. This repository's default branch is `main`. If the target does not show `main`, add **Include by pattern** and type `main`.
8. Under **Branch protections**, confirm these rules. GitHub turns the first two on for a new ruleset. Turn the last two on yourself:
   - **Restrict deletions**
   - **Block force pushes**
   - **Require a pull request before merging**. Set the required number of approvals to **0**. If that field starts at 1, change it to 0 before you save. Leave "Require review from Code Owners" off. This repo has no `CODEOWNERS` file.
   - **Require status checks to pass before merging**.
9. In the status-check box, add these two names, one at a time:
   - `Python tests`
   - `Terraform and Ansible`

   On a pull request they are listed under the workflow named CI. Type the job name. If the search list only offers `CI / Python tests` and `CI / Terraform and Ansible`, add those full strings. The name has to match the check on the pull request. Do not add a check named only `CI`.

   If the list is empty, the workflow has not reported those checks on this repository yet. Open the **Actions** tab, confirm a run of CI has finished on `main`, then come back and search again. You can also type the name and add it before a run exists.

   If adding a check asks which app is the source, choose **GitHub Actions**. That ties the check to this workflow. **Any source** would also accept a status posted by hand by someone with write access.

10. **Require branches to be up to date before merging** starts out checked. Clear it. Checked, GitHub refuses to merge until the pull request contains the latest `main`, which means an extra CI run every time `main` moves. With one maintainer that mostly adds waiting. You can turn it on later.
11. Leave the other rules off (signed commits, linear history, lock branch).
12. Click **Create**.

The ruleset takes effect as soon as it is Active.

### Classic branch protection rule

Use this only if you are looking at **Settings → Branches** instead of Rulesets. One of the two is enough. Do not create both for `main`; they stack and the stricter one wins.

1. **Settings → Branches** (under **Code, planning, and automation**).
2. **Add classic branch protection rule**.
3. Branch name pattern: `main`.
4. Check **Require a pull request before merging**. Do not check **Require approvals**.
5. Check **Require status checks to pass before merging**. Add `Python tests` and `Terraform and Ansible`, the same names as above. Clear **Require branches to be up to date before merging** if it is checked.
6. Leave **Do not allow bypassing the above settings** unchecked. Unchecked is admin bypass on: an admin can still merge or push around the rule. Checking it would apply the rule to you as well.
7. Leave **Allow force pushes** unchecked. Unchecked means force pushes are blocked.
8. Leave **Allow deletions** unchecked. Unchecked means the branch cannot be deleted.
9. Click **Create**.

### If you rename a job

The ruleset stores the check name as text. If you change `name:` on either job in `.github/workflows/ci.yml`, edit the ruleset to match. Otherwise `main` waits for a check that will never run.

The infrastructure job is still named **Terraform and Ansible** after Ansible was removed. The name is the string this ruleset matches. Leave it until you edit both the workflow and the ruleset in the same change. The image workflow is not a required check.

## 2. Dependabot alerts and security updates

`.github/dependabot.yml` schedules ordinary version updates (weekly, minor and patch grouped, at most five open version pull requests per ecosystem). It does not, by itself, turn on vulnerability alerts.

Alerts and security updates protect against a different event: a version you already use shows up in the GitHub Advisory Database as a known vulnerability. An alert is a private notice on the **Security and quality** tab. A security update is a pull request that bumps that dependency to a patched version. You still review and merge it. Those security pull requests are not held back by the cap of five in `dependabot.yml`.

The dependency graph is already on for a public repository. Alerts need it. You cannot turn the graph off here, and you do not need to.

1. **Settings**.
2. In the left sidebar, under **Security and quality**, click **Advanced Security**.
3. Next to **Dependabot alerts**, click **Enable** if it is off.
4. Next to **Dependabot security updates**, click **Enable** if it is off. On a public repository this feature is often already on and the button does nothing. Leave it on.

Version updates are the `dependabot.yml` file, not a second switch. If this page offers to create a new `dependabot.yml`, skip that. The committed file is the one to keep.

After this file is on `main`, **Insights → Dependency graph → Dependabot** lists the pip, GitHub Actions, Terraform, and Docker entries. The first run happens when the file is added, and then every Monday. The Docker entry tracks the `python:3.11-slim` digest in the Dockerfile. It does not move the game-night `app_image` pin.

Dependabot alerts for GitHub Actions are raised for actions pinned to a version tag such as `@v7`. They are not raised for an action pinned to a commit SHA. The pins in `.github/workflows/ci.yml` are SHAs on purpose (see the comment in that file). The weekly `github-actions` entry in `dependabot.yml` is what moves those pins when a new release is published.

## 3. Secret scanning and push protection

These are free on a public repository. You do not need a paid plan.

Secret scanning protects against a credential that is already in the repository: an AWS access key, a GitHub token, a Clerk secret, or another pattern GitHub recognizes. GitHub scans the current files and the history, and opens a private alert. The alert is not shown on the public page.

Push protection protects against the same kind of secret on the way in. A `git push`, a commit made in the web editor, or a file upload that contains a recognized secret is rejected, and GitHub tells you which line. Remove the secret and push again. A secret GitHub has no pattern for can still land. `.env`, `terraform.tfvars`, and `*.pem` are gitignored; push protection is the backstop when a key is pasted into a file that is not ignored.

1. **Settings → Advanced Security** (same page as the Dependabot switches).
2. To the right of **Secret Protection**, click **Enable**, read the dialog, and confirm **Enable Secret Protection**. That turns on secret scanning alerts.
3. In the **Secret Protection** section, to the right of **Push protection**, click **Enable**.

If a dialog asks you to buy GitHub Secret Protection or start a trial, cancel it. That product is how private repositories get the same scans. This repository is public, and secret scanning alerts plus push protection are free here. After you cancel, the Advanced Security page should still let you enable them.

When an alert appears, revoke that credential at the place it was issued (AWS, Clerk, GitHub), then remove it from the file and from git history if it was committed. Deleting the line in a new commit leaves the old commit readable.

## 4. Private vulnerability reporting

`SECURITY.md` tells a reporter to use GitHub's private form. The file does not enable the form. This switch does.

It protects against a public bug report that includes how to exploit the bug. Sign-in bypass, a way to read another person's game, or a leaked-key path should reach you before they reach everyone who can see the issues list. The report stays on the **Security and quality** tab, under **Advisories**, until you publish an advisory.

1. **Settings → Advanced Security**.
2. Next to **Private vulnerability reporting**, click **Enable**.

Confirm the form exists: open the **Security and quality** tab, then **Advisories**. You should see a way to report a vulnerability. The text from `SECURITY.md` is shown above the form.

## 5. The `game-night` environment

The Game night workflow runs only when someone clicks **Run workflow**, and only after a required reviewer approves that run. The reviewer, the role ARN, and the secrets are settings on the environment. They are not in git. Merging the workflow file does not create them.

Create the environment yourself before the first run. If the first run creates `game-night` for you, that run is not reviewed.

The workflow assumes `dutch-blitz-gha-deployer` with GitHub's OIDC token. `AWS_ROLE_ARN` is an environment variable, not a secret. There is no stored AWS access key. The role's permissions are the two bot policies, so a run can do what `up` and `down` can do, including root commands on instances tagged `Project=dutch-blitz`. The trust policy is what limits who can assume it: the subject is exactly `repo:sixOfOne@50211093/dutch-blitz@1413603165:environment:game-night`, `aud` is `sts.amazonaws.com`, `event_name` is `workflow_dispatch`, `ref` is `refs/heads/main`, and `workflow_ref` is this workflow file on `main`. This repository was created on 2026-10-10 and GitHub sets `use_immutable_subject`, so the token carries those ids. A pull request workflow cannot assume the role.

Do these steps in order, after `terraform/gha-deployer` is applied with your own credentials. The apply commands are in the README and in [bot-iam.md](bot-iam.md).

1. Confirm `terraform/iam-bot` is already applied, so `grok-bot-dutch-blitz-compute` and `grok-bot-dutch-blitz-records` exist.
2. Copy `terraform/gha-deployer/terraform.tfvars.example` to `terraform/gha-deployer/terraform.tfvars`. Set `enable_aws = true`. Set `github_oidc_provider_arn` if the account already has a provider for `token.actions.githubusercontent.com` (`aws iam list-open-id-connect-providers`). Leave it empty to create the provider. Leave `bot_user_name` unless the bot user was renamed.
3. `terraform -chdir=terraform/gha-deployer init -backend-config=../backend.hcl`, then `terraform -chdir=terraform/gha-deployer apply -var-file=terraform.tfvars`. Read `role_arn`.
4. **Settings → Environments → New environment**. Name it exactly `game-night`.
5. Turn on **Required reviewers** and add yourself. Save before any workflow run.
6. Under **Deployment branches and tags**, choose **Selected branches** and add `main`.
7. Add an environment **variable** named `AWS_ROLE_ARN`. Paste the `role_arn`. Do not put that ARN in a secret.
8. Add environment **secrets** `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `TERRAFORM_TFVARS`, and `BACKEND_HCL`. `TERRAFORM_TFVARS` is the body of the gitignored `terraform/terraform.tfvars`, without Clerk keys and without `app_image`. `BACKEND_HCL` is the body of the gitignored `terraform/backend.hcl`. The workflow writes those two files in the runner temp directory and does not echo them.
9. **Actions → Game night → Run workflow**. Choose branch `main` and action `status`. Approve the pending deployment. Then run `up`, `backup`, or `down` the same way. The concurrency group is `game-night` and a second run waits. The role session is one hour.

