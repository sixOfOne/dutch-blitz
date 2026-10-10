# Bot IAM user

`terraform/iam-bot` creates an IAM user named `grok-bot-dutch-blitz` and attaches one customer managed policy. It also creates a monthly cost budget of $10 and emails a gitignored address at 80% and at 100% of that amount.

Apply it yourself, after the state bucket exists. The bot's policy cannot create users, create or attach policies, or modify budgets, so the bot cannot apply this root.

```bash
cp terraform/iam-bot/terraform.tfvars.example terraform/iam-bot/terraform.tfvars
# Edit the copy. Real account id, bucket names, zone id, and email.
terraform -chdir=terraform/iam-bot init -backend-config=../backend.hcl
terraform -chdir=terraform/iam-bot apply -var-file=terraform.tfvars
```

Apply refuses the example account id `000000000000`, the example zone id `Z000000000000EXAMPLE`, the `replace-me` bucket names, and any `alerts@example.com` address. Those strings stay in git. The real values stay in `terraform/iam-bot/terraform.tfvars`, which is gitignored.

AWS sends the budget address a confirmation message. The alarm does not send until that message is confirmed. The budget is account-wide: a night is about $0.10, and $10 is the line for a forgotten instance. Raise `monthly_budget_usd` if this account pays for other work. The bot can view this budget. It cannot change it.

The access key is not a Terraform resource. Creating it in Terraform would store the secret in the state file. After apply, create the key in the console or with the CLI and put it in the assistant's secret store. Rotation is in the README.

## The policy file

[grok-bot-dutch-blitz-policy.json](grok-bot-dutch-blitz-policy.json) is the policy. `terraform/iam-bot` reads that file and replaces the placeholders below. Edit the JSON, then apply this root again with your own credentials.

| Placeholder in the JSON | Replaced from |
| --- | --- |
| `000000000000` | `aws_account_id` |
| `dutch-blitz-tfstate-replace-me` | `state_bucket_name` |
| `dutch-blitz-scores-replace-me` | `backup_bucket_name` |
| `Z000000000000EXAMPLE` | `dns_zone_id` |
| `dutch-blitz-game` | `<name_prefix>-game` |
| `key-pair/neo-atari` | `key-pair/<key_name>` |
| `blitz.*` | `<dns_record_name>.*` |
| `dutch-blitz-monthly` | `budget_name` |
| `us-east-2` | `aws_region` |

There is no email in the JSON. The address is only an argument of the budget resource. That is also why the bot cannot `s3:GetObject` the `dutch-blitz/iam-bot/terraform.tfstate` key: the state for this root contains the address.

An inline policy on a user is limited to 2,048 characters. A managed policy allows 6,144, and this document is larger than that as well. Apply attaches it as two managed policies, `grok-bot-dutch-blitz-compute` (EC2 and the game role, through `PassGameRoleToEc2Only`) and `grok-bot-dutch-blitz-records` (Route 53, S3, cost, and the budget view). The split is the quota. It is not a second set of rights. Whitespace does not count toward the quota. A precondition on the root refuses a half that grows past 6,144.

## What `up` and `down` are allowed to do

`up` applies `terraform/backup` (create the snapshot bucket if it is missing) and `terraform/` (instance, security group, role, instance profile, and the `blitz` A record). `down` destroys only the game-night stack. It does not destroy the backup bucket or the state bucket.

EC2 describes, including the key pair, are allowed in `us-east-2` only (`aws:RequestedRegion`). `RunInstances` for the instance and its volume must request the tag `Project=dutch-blitz`, which is what the game-night stack sets. The key pair resource on that call is only `neo-atari` (or whatever `key_name` is). `CreateKeyPair`, `ImportKeyPair`, and `DeleteKeyPair` are absent, so the bot cannot replace the key.

`TerminateInstances` and `DeleteSecurityGroup` require the same `Project` tag. Security group rule changes require it too. The bot cannot delete an unrelated instance or group in the region.

The instance role and instance profile are only `dutch-blitz-game`. `iam:PassRole` is only that role, and only when the service is `ec2.amazonaws.com`. The inline role policy (`iam:PutRolePolicy` / `iam:DeleteRolePolicy`) is how the instance is allowed to read and write the backup bucket. That is role policy on this one role. It is not `iam:CreatePolicy`, `iam:AttachUserPolicy`, `iam:AttachRolePolicy`, or `iam:PutUserPolicy`. Those are absent on purpose.

Route 53 changes are allowed only on the one hosted zone, only when every name in the change matches `blitz.*`, only for type `A`, and only for `CREATE`, `UPSERT`, or `DELETE`. Two `Deny` statements sit in front of that: any change whose name matches the Clerk labels (`clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`), and any change of type `NS`, `SOA`, or `CNAME` in that zone. A deny wins over the allow. The zone itself is not created or deleted. There is no `route53:CreateHostedZone`, `route53:DeleteHostedZone`, or `route53:ChangeTagsForResource`.

S3 on the backup bucket is list, location, the configuration calls Terraform makes (versioning, encryption, public access block, tags), `CreateBucket` for the first `up`, and `GetObject` / `PutObject` on objects. There is no `s3:DeleteBucket`, `s3:DeleteObject`, `s3:DeleteObjectVersion`, or `s3:PutBucketPolicy`. `PutObject` can overwrite a key. The backup bucket has versioning, so the previous snapshot remains, and the bot cannot delete that version.

S3 on the state bucket is `ListBucket` and `GetBucketLocation` on the bucket, `GetObject` and `PutObject` on the game-night and backup state keys, and `GetObject`, `PutObject`, and `DeleteObject` on those two `.tflock` objects. HashiCorp's S3 backend does not delete the state object itself. `DeleteObject` is what releases the lock. There is no `DeleteObject` on the state keys, no `DeleteObjectVersion`, and no `DeleteBucket`.

`ce:GetCostAndUsage` and `budgets:ViewBudget` are read-only. The budget action is limited to the one budget ARN.

## What is not scoped as tightly as the name suggests

These are the permissions I would not call precise, and why they are shaped this way.

**`iam:PutRolePolicy` on `dutch-blitz-game`.** IAM cannot constrain the JSON of an inline role policy. The bot could put a broader document on that role. It can pass the role only to EC2, and this policy has no SSM or key-pair write, so it still cannot download the private key or open a session as the instance. A permissions boundary on the role would cap that, regardless of the inline document. I did not add one: the game-night role already exists, and the boundary would be a new policy the next apply would attach. That is a deliberate follow-up, not something this policy can express by itself.

**`ec2:RunInstances` on the subnet, security group, network interface, image, and snapshot.** `RunInstances` is one API call that names all of those. IAM applies a tag condition to every resource in the call. Only the instance and the volume are tagged `Project=dutch-blitz`. Requiring the tag on the subnet or the AMI would deny the launch. The key pair is the exception that can be named exactly. The image and snapshot ARNs are wildcards because the AMI id changes when Amazon Linux 2023 publishes a new one; the data source picks the latest. I included both `arn:aws:ec2:us-east-2::image/*` (no account id, which is how public AMIs are written) and the account-scoped image ARN. I am not sure which form this provider's `RunInstances` check uses. Neither action deregisters an AMI or deletes a snapshot; those are different actions and they are not granted. The snapshot permission is there because launching from an EBS AMI checks the snapshot resource, and I would rather include that read-side resource than have the first `up` fail on it.

**`ec2:CreateSecurityGroup`.** The same tag footgun: the call also names the VPC, and the VPC does not receive `Project=dutch-blitz`. A `aws:RequestTag` condition denies it. The statement is limited to `us-east-2` and to security-group plus VPC resources. The bot could create a group in any VPC in that region. Deleting a group still requires the `Project` tag, so it cannot delete one it did not tag.

**`ec2:Describe*`.** Describe actions do not support resource-level permissions. The region condition is the restriction that does work. This includes `DescribeKeyPairs` for every key pair in `us-east-2`, not only `neo-atari`. `DescribeKeyPairs` returns names and fingerprints, not the private key. Create and delete of key pairs are not granted.

**`ec2:CreateTags`, `ec2:DeleteTags`, `ec2:ModifyInstanceAttribute`, and the instance-profile association actions.** These are region-scoped, not tag-scoped. The provider sometimes tags and attaches the profile in the same moment it creates the instance, and a tag condition on that call fails closed. I am not sure `AssociateIamInstanceProfile`, `ReplaceIamInstanceProfileAssociation`, and `DisassociateIamInstanceProfile` all run on a normal apply: the profile is usually set inside `RunInstances`. They are included so a later change of profile does not fail. They do not include `iam:PassRole`; that is the separate statement.

**`ec2:ModifyVolume` and `ec2:DeleteVolume` are absent.** I am not sure a later change of `root_volume_gb` needs `ModifyVolume`. Destroy relies on `delete_on_termination` inside `TerminateInstances`, which should not need a separate `DeleteVolume`. If a destroy fails on `DeleteVolume`, that is the action to add, with the `Project` tag condition, rather than a blanket volume delete.

**`route53:ListHostedZones` and `route53:ListHostedZonesByName`.** The game-night stack looks the zone up by name. Neither action accepts a hosted zone ARN, so the resource is `*`. The bot can see the name and id of every zone in the account. It cannot change a zone that is not the one in `dns_zone_id`.

**`route53:GetChange` on `arn:aws:route53:::change/*`.** Terraform polls the change it just submitted. The change id is not knowable in the policy, and the action cannot be limited to "changes this user made." It can read the status of any change id. It cannot submit one; that is `ChangeResourceRecordSets`, which is limited.

**`route53:ListResourceRecordSets` on the zone.** There is no IAM action for reading a single record. A read of the zone returns the Clerk CNAMEs as well. The allow and the two denies are what stop writes. `ForAllValues` matches a request that omits the condition key entirely (an empty set passes). AWS includes those keys on `ChangeResourceRecordSets`. The explicit denies are there so a later edit that widens the allow still cannot change the Clerk names or an NS, SOA, or CNAME.

**`s3:ListBucket` on the state bucket, without a prefix condition.** HashiCorp's own example puts the bucket name inside `s3:prefix`, which is not the prefix Terraform sends. A prefix that does not match the list call fails `init` in a way that looks like a missing bucket. Object reads and writes are still limited to the two state keys and their lock objects. `ListBucket` reveals other key names in the bucket, including `dutch-blitz/iam-bot/terraform.tfstate`, and not the object contents.

**`s3:CreateBucket` on the backup bucket ARN only.** Some older notes say `CreateBucket` is only honored on `*`. Resource-level permissions for that action do exist. I did not grant `*` because that is permission to create any bucket. If the first `up` returns `AccessDenied` on `CreateBucket` and the bucket name in the policy matches the tfvars, the `*` form is the thing you would be deciding to accept. I would not accept it without a name condition.

**Backup-bucket reads the provider might not need.** `GetObjectLockConfiguration` is included because a refresh of `aws_s3_bucket` has asked for it in some provider versions. I did not grant `GetBucketAcl`, `GetBucketPolicy`, `GetBucketCors`, `GetBucketWebsite`, `GetBucketLogging`, `GetLifecycleConfiguration`, `GetReplicationConfiguration`, `GetAccelerateConfiguration`, or `GetBucketRequestPayment`. Provider 5 split those into resources this stack does not manage. If a refresh says `AccessDenied` on one of them, add that read and not a write.

**`s3:DeleteObject` only on the two `.tflock` keys.** Current Terraform does not delete the state object. A future release that did would fail closed here. Versioning would still keep the bytes if `DeleteObject` on the state key ever had to be added. `s3:DeleteObjectVersion` should stay absent so a delete cannot become a permanent one.

**No `aws:RequestedRegion` on IAM, Route 53, S3, Budgets, or Cost Explorer.** EC2 is the API that lives in `us-east-2`. IAM and Route 53 are global. S3 is often called on a regional endpoint and sometimes not, and a region condition has denied ordinary bucket calls. Cost Explorer and Budgets are served from `us-east-1`. A `us-east-2` condition on those statements would make `up` fail, and would make the cost read fail. `ce:GetCostAndUsage` has no resource ARN, so its resource is `*`. I did not grant `aws-portal:ViewBilling`. I am not sure a new account returns cost data before Cost Explorer is opened once in the billing console; that click is yours, not the bot's. `budgets:ViewBudget` is scoped to the one budget. I am not sure a list-all-budgets call succeeds against a single ARN. A read of this budget should. I did not widen it to every budget.

## What is refused

No `iam:CreateUser`, `iam:CreateAccessKey`, `iam:CreatePolicy`, `iam:CreatePolicyVersion`, `iam:AttachUserPolicy`, `iam:AttachRolePolicy`, `iam:AttachGroupPolicy`, `iam:PutUserPolicy`, `iam:PutGroupPolicy`, or `iam:*`. No `s3:DeleteBucket` and no bucket policy writes. No `dynamodb:*`. No `budgets:ModifyBudget`. No admin policy and no `Action: "*"`.
