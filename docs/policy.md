# Policy: facts and decisions still needed

Reviewed **2026-10-16**. These are paraphrases of current official pages, not a promise
that policy will remain unchanged. Recheck the sources before deploying.

## Documented facts

The [ASU scratch page](https://docs.rc.asu.edu/scratch/) says scratch is temporary,
not backed up, and files without access for 90 days will be removed. It directs
longer-lived data to home, purchased project storage, or another medium, and describes
notifications and an exception process. An age notification is a reason to act promptly,
not a reason to wait for this software's next run.

The [ASU acceptable-use policy, sections 11.2–11.4](https://cores.research.asu.edu/computing-and-data-services/research-computing/policies/)
defines active use as access/modification within 90 days or designation as a reference
source dataset for jobs. It prohibits compute jobs on login nodes, disruptive workloads,
sleep loops with excessive idleness, and unattended listening services. Home is
persistent, quota-limited space intended for software and environments. Users remain
responsible for data protection; RC retains authority over storage and enforcement.

[ASU job scheduling](https://docs.rc.asu.edu/schedulers/) documents Slurm batch jobs
that run without continuous user input. The [partition guide](https://docs.rc.asu.edu/partitions-and-qos/)
currently describes `lightwork` as including bulk file operations. That is a candidate
to discuss with RC, not a selected partition, allocation, QoS, or authorization in this
project. The wrappers intentionally contain no site resource defaults.

## These pages do not resolve the deployment question

Neither page explicitly permits artificial periodic access or explicit timestamp
updates to prolong scratch retention. This project does **not** interpret the active-use
definition as such permission. Keep `refresh_enabled=false` and
`scheduler_enabled=false` until Research Computing decides your case. Audit and dry-run
development can continue independently; use approved resources even for a large audit.

Ask RC, through its [official support entrypoint](https://docs.rc.asu.edu/contact-us/), to confirm:

1. Is this use of selected, actively needed research scratch data permitted? Do periodic
   one-byte reads or explicit atime changes qualify? Is a reference-dataset designation
   or an exception the appropriate route instead? Record the approved scope and expiry.
2. Which scratch system/mount is involved, and which timestamps or other criteria does
   its purge actually use? Are file and directory ages treated differently? What are the
   notification, eligibility, scan and deletion schedules? Do not infer implementation
   from the phrase “accessed.”
3. What atime behavior, metadata caching/delayed writeback, precision and client views
   apply? Does purge observe the same metadata as a compute client? How should empty files
   and any ctime change be handled? May the disposable probe be used there?
4. Which compute/transfer resource, partition, account, QoS, limits, and I/O rates should
   an inventory or approved refresh use? Does that node have the actual filesystem mount?
5. Is a lightweight daily trigger allowed, where should it run, and is user cron supported
   and durable through host replacement/maintenance? If not, what RC-supported recurring
   scheduler can call `trigger`? Is querying/submitting Slurm from it permitted?
6. Which persistent location supports atomic rename, SQLite locking and `flock` across
   the trigger and job nodes? What monitoring/notification mechanism is supported?

Record the response privately in `site_approval` / `scheduler_approval` (e.g., a private
request reference, scope, approved method and expiry), never in this repository. These
strings are operator attestations; the software cannot authenticate or renew permission.
`behavior_confirmed=true` separately records that the chosen method has been validated
with disposable data and the site. Approval is specific to the method and scope: reset
these switches and re-confirm whenever those change. They are not an access-control
substitute for site policy.

## Alternatives and data protection

If artificial refresh is not permitted, use the audit to prioritize an RC-approved
retention plan. Ask about reference datasets, policy exceptions or extensions, and storage
sized to your project. [Project storage](https://docs.rc.asu.edu/project-storage/) offers
persistent `/data` storage and snapshots, but its documentation says it is not replicated
to a second system. [Canyon](https://docs.rc.asu.edu/canyon-tape-storage/) is cold storage
with variable recall time and no secondary replication. The [filesystem overview](https://docs.rc.asu.edu/file-system-overview/)
helps compare those services and home quotas. Confirm current suitability, cost, data
classification and backup needs with RC before choosing.

File access is not a backup: it creates no independent copy, repairs no corruption,
and prevents neither hardware failure nor administrative removal. Keep important data
in an approved durable location with separately verified backups. This software does
not move or copy research data, change content or mtime, or delete research files.
