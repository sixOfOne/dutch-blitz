# Bot IAM user

`terraform/iam-bot` creates an IAM user named `grok-bot-dutch-blitz` and attaches two customer managed policies. It also creates a monthly cost budget of $10 and emails a gitignored address at 80% and at 100% of that amount.

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

An inline policy on a user is limited to 2,048 characters. A managed policy allows 6,144. The rendered document is about 5,900 non-whitespace characters: under one managed policy, and over the inline limit. Apply still attaches two managed policies, `grok-bot-dutch-blitz-compute` (EC2, `iam:GetInstanceProfile`, and `PassGameRoleToEc2Only`) and `grok-bot-dutch-blitz-records` (Route 53, S3, cost, and the budget view). The two resources stay so a later statement does not force a new policy address, and so each half stays well under 6,144. The split is the quota. It is not a second set of rights. Whitespace does not count toward the quota. A precondition on the root refuses a half that grows past 6,144.

If this root was already applied with the older policy, apply it again with your own credentials. That replaces the two managed policies. The bot cannot do that apply: it has no `iam:CreatePolicy` and no `iam:AttachUserPolicy`. Until you re-apply, the previous document, including `iam:PutRolePolicy`, is still what AWS enforces.

## What `up` and `down` are allowed to do

`up` applies `terraform/backup` (create the snapshot bucket if it is missing) and `terraform/` (instance, security group, and the `blitz` A record). It does not apply `terraform/game-role`. That root is the instance role and instance profile, and only you apply it. The game-night stack looks the profile up by name. `down` destroys only the game-night stack. It does not destroy the backup bucket, the instance role, or the state bucket.

EC2 describes, including the key pair, are allowed in `us-east-2` only (`aws:RequestedRegion`). `RunInstances` for the instance and its volume must request the tag `Project=dutch-blitz`, which is what the game-night stack sets. The key pair resource on that call is only `neo-atari` (or whatever `key_name` is). `CreateKeyPair`, `ImportKeyPair`, and `DeleteKeyPair` are absent, so the bot cannot replace the key.

`TerminateInstances` and `DeleteSecurityGroup` require the same `Project` tag. Security group rule changes require it too. The bot cannot delete an unrelated instance or group in the region.

The only IAM actions are `iam:GetInstanceProfile` on `instance-profile/dutch-blitz-game` and `iam:PassRole` on `role/dutch-blitz-game`, and `PassRole` only when `iam:PassedToService` is `ec2.amazonaws.com`. The profile name is what `RunInstances` receives. Passing any other role is denied, so the bot cannot launch with a profile whose role is not this one. There is no `iam:CreateRole`, `iam:DeleteRole`, `iam:UpdateAssumeRolePolicy`, `iam:PutRolePolicy`, `iam:DeleteRolePolicy`, `iam:CreateInstanceProfile`, `iam:AddRoleToInstanceProfile`, or `iam:RemoveRoleFromInstanceProfile`. The inline policy that lets the instance read and write the backup bucket is written by `terraform/game-role`, with your credentials, and it is not in a state object the bot can read or write.

Route 53 changes are allowed only on the one hosted zone, only when every name in the change matches `blitz.*`, only for type `A`, and only for `CREATE`, `UPSERT`, or `DELETE`. Two `Deny` statements sit in front of that: any change whose name matches the Clerk labels (`clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`), and any change of type `NS`, `SOA`, or `CNAME` in that zone. A deny wins over the allow. The zone itself is not created or deleted. There is no `route53:CreateHostedZone`, `route53:DeleteHostedZone`, or `route53:ChangeTagsForResource`.

S3 on the backup bucket is list, location, the configuration calls Terraform makes (versioning, encryption, public access block, tags), `CreateBucket` for the first `up`, and `GetObject` / `PutObject` on objects. There is no `s3:DeleteBucket`, `s3:DeleteObject`, `s3:DeleteObjectVersion`, or `s3:PutBucketPolicy`. `PutObject` can overwrite a key. The backup bucket has versioning, so the previous snapshot remains, and the bot cannot delete that version.

S3 on the state bucket is `ListBucket` and `GetBucketLocation` on the bucket, `GetObject` and `PutObject` on the game-night and backup state keys, and `GetObject`, `PutObject`, and `DeleteObject` on those two `.tflock` objects. HashiCorp's S3 backend does not delete the state object itself. `DeleteObject` is what releases the lock. There is no `DeleteObject` on the state keys, no `DeleteObjectVersion`, and no `DeleteBucket`.

`ce:GetCostAndUsage` and `budgets:ViewBudget` are read-only. The budget action is limited to the one budget ARN.

## What is not scoped as tightly as the name suggests

These are the permissions I would not call precise, and why they are shaped this way.

**The game role is no longer something the bot can edit.** The old policy granted `iam:PutRolePolicy` on `dutch-blitz-game` next to `iam:PassRole` and `ec2:RunInstances`. IAM cannot constrain the JSON of an inline role policy, so the bot could have replaced that document, launched an instance with the widened role, and read the instance credentials. The role, its inline policy, and its instance profile now live in `terraform/game-role`. `up` does not apply that root, and the bot cannot `GetObject` or `PutObject` its state key. The bot's remaining IAM calls are the data-source read and `PassRole` to EC2. A permissions boundary is not on the role. The bot cannot change the document, so the boundary is not what closes this path.

**`iam:GetInstanceProfile` is the only IAM read.** The AWS provider's refresh of `data.aws_iam_instance_profile` calls that action, and the response includes the role name. I did not grant `iam:GetRole`, `iam:GetRolePolicy`, or `iam:ListRolePolicies`. If a refresh returns `AccessDenied` on `iam:GetRole` and names no other action, add that one read on `role/dutch-blitz-game` and nothing else.

**`ec2:RunInstances` on the subnet, security group, network interface, image, and snapshot.** `RunInstances` is one API call that names all of those. IAM applies a tag condition to every resource in the call. Only the instance and the volume are tagged `Project=dutch-blitz`. Requiring the tag on the subnet or the AMI would deny the launch. The key pair is the exception that can be named exactly. The image and snapshot ARNs are wildcards because the AMI id changes when Amazon Linux 2023 publishes a new one; the data source picks the latest. I included both `arn:aws:ec2:us-east-2::image/*` (no account id, which is how public AMIs are written) and the account-scoped image ARN. I am not sure which form this provider's `RunInstances` check uses. Neither action deregisters an AMI or deletes a snapshot; those are different actions and they are not granted. The snapshot permission is there because launching from an EBS AMI checks the snapshot resource, and I would rather include that read-side resource than have the first `up` fail on it.

**`ec2:CreateSecurityGroup`.** The same tag footgun: the call also names the VPC, and the VPC does not receive `Project=dutch-blitz`. A `aws:RequestTag` condition denies it. The statement is limited to `us-east-2` and to security-group plus VPC resources. The bot could create a group in any VPC in that region. Deleting a group still requires the `Project` tag, so it cannot delete one it did not tag.

**`ec2:Describe*`.** Describe actions do not support resource-level permissions. The region condition is the restriction that does work. This includes `DescribeKeyPairs` for every key pair in `us-east-2`, not only `neo-atari`. `DescribeKeyPairs` returns names and fingerprints, not the private key. Create and delete of key pairs are not granted.

**`ec2:CreateTags` and `ec2:DeleteTags`.** These are region-scoped, not tag-scoped. The provider tags the instance in the same moment it creates it, and a tag condition on that call fails closed. `ec2:AssociateIamInstanceProfile`, `ec2:ReplaceIamInstanceProfileAssociation`, `ec2:DisassociateIamInstanceProfile`, and `ec2:ModifyInstanceAttribute` are not granted. The profile is an argument of `RunInstances`, so a later call that attaches a profile is not required for `up`. Without those association actions, a running instance cannot be given a different profile. `PassRole` is still limited to `dutch-blitz-game`, so a `RunInstances` that names some other profile fails unless that profile's role is exactly this one. `ModifyInstanceAttribute` is how an in-place instance-type change is done. Changing `instance_type` in tfvars and applying will fail closed on that action. Replace the instance (`down`, then `up`) if the type should change.

**`ec2:ModifyVolume` and `ec2:DeleteVolume` are absent.** I am not sure a later change of `root_volume_gb` needs `ModifyVolume`. Destroy relies on `delete_on_termination` inside `TerminateInstances`, which should not need a separate `DeleteVolume`. If a destroy fails on `DeleteVolume`, that is the action to add, with the `Project` tag condition, rather than a blanket volume delete.

**`route53:ListHostedZones` and `route53:ListHostedZonesByName`.** The game-night stack looks the zone up by name. Neither action accepts a hosted zone ARN, so the resource is `*`. The bot can see the name and id of every zone in the account. It cannot change a zone that is not the one in `dns_zone_id`.

**`route53:GetChange` on `arn:aws:route53:::change/*`.** Terraform polls the change it just submitted. The change id is not knowable in the policy, and the action cannot be limited to "changes this user made." It can read the status of any change id. It cannot submit one; that is `ChangeResourceRecordSets`, which is limited.

**`route53:ListResourceRecordSets` on the zone.** There is no IAM action for reading a single record. A read of the zone returns the Clerk CNAMEs as well. The allow and the two denies are what stop writes. `ForAllValues` matches a request that omits the condition key entirely (an empty set passes). AWS includes those keys on `ChangeResourceRecordSets`. The explicit denies are there so a later edit that widens the allow still cannot change the Clerk names or an NS, SOA, or CNAME.

**`s3:ListBucket` on the state bucket, without a prefix condition.** HashiCorp's own example puts the bucket name inside `s3:prefix`, which is not the prefix Terraform sends. A prefix that does not match the list call fails `init` in a way that looks like a missing bucket. Object reads and writes are still limited to the two state keys and their lock objects. `ListBucket` reveals other key names in the bucket, including `dutch-blitz/iam-bot/terraform.tfstate` and `dutch-blitz/game-role/terraform.tfstate`, and not the object contents. The game-role object is the one that holds the instance role's policy. The bot cannot read it.

**`s3:CreateBucket` on the backup bucket ARN only.** Some older notes say `CreateBucket` is only honored on `*`. Resource-level permissions for that action do exist. I did not grant `*` because that is permission to create any bucket. If the first `up` returns `AccessDenied` on `CreateBucket` and the bucket name in the policy matches the tfvars, the `*` form is the thing you would be deciding to accept. I would not accept it without a name condition.

**Backup-bucket reads the provider might not need.** `GetObjectLockConfiguration` is included because a refresh of `aws_s3_bucket` has asked for it in some provider versions. I did not grant `GetBucketAcl`, `GetBucketPolicy`, `GetBucketCors`, `GetBucketWebsite`, `GetBucketLogging`, `GetLifecycleConfiguration`, `GetReplicationConfiguration`, `GetAccelerateConfiguration`, or `GetBucketRequestPayment`. Provider 5 split those into resources this stack does not manage. If a refresh says `AccessDenied` on one of them, add that read and not a write.

**`s3:DeleteObject` only on the two `.tflock` keys.** Current Terraform does not delete the state object. A future release that did would fail closed here. Versioning would still keep the bytes if `DeleteObject` on the state key ever had to be added. `s3:DeleteObjectVersion` should stay absent so a delete cannot become a permanent one.

**No `aws:RequestedRegion` on IAM, Route 53, S3, Budgets, or Cost Explorer.** EC2 is the API that lives in `us-east-2`. IAM and Route 53 are global. S3 is often called on a regional endpoint and sometimes not, and a region condition has denied ordinary bucket calls. Cost Explorer and Budgets are served from `us-east-1`. A `us-east-2` condition on those statements would make `up` fail, and would make the cost read fail. `ce:GetCostAndUsage` has no resource ARN, so its resource is `*`. I did not grant `aws-portal:ViewBilling`. I am not sure a new account returns cost data before Cost Explorer is opened once in the billing console; that click is yours, not the bot's. `budgets:ViewBudget` is scoped to the one budget. I am not sure a list-all-budgets call succeeds against a single ARN. A read of this budget should. I did not widen it to every budget.

## What is refused

No `iam:CreateUser`, `iam:CreateAccessKey`, `iam:CreatePolicy`, `iam:CreatePolicyVersion`, `iam:AttachUserPolicy`, `iam:AttachRolePolicy`, `iam:AttachGroupPolicy`, `iam:PutUserPolicy`, `iam:PutGroupPolicy`, `iam:PutRolePolicy`, `iam:CreateRole`, `iam:UpdateAssumeRolePolicy`, `iam:CreateInstanceProfile`, or `iam:*`. No `ec2:AssociateIamInstanceProfile`, `ec2:ReplaceIamInstanceProfileAssociation`, or `ec2:ModifyInstanceAttribute`. No `s3:DeleteBucket` and no bucket policy writes. No `dynamodb:*`. No `budgets:ModifyBudget`. No admin policy and no `Action: "*"`. The only `iam:` actions in the document are `iam:GetInstanceProfile` and `iam:PassRole`.
