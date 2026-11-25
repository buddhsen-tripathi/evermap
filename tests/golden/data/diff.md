## Model changes

1 added, 0 removed, 3 modified (0 renamed or moved), 0 re-anchored, 12 unchanged.

### Added

- `role:recipient` Recipient

### Modified

- `operation:parcel.collect` Collect parcel
  - `$.relations[type=reads,target=entity:locker]`: — → {"target":"entity:locker","type":"reads"}
- `operation:parcel.deposit` Deposit parcel
  - `$.auth.authentication`: required → optional
  - `$.summary`: Assigns a free locker and stores the parcel. → Stores the parcel in a free locker.
- `workflow:deposit-parcel` Deposit a parcel
  - `$.steps[id=s2].plain`: Put the parcel in a free locker. → Put the parcel in the nearest free locker.

### Relations added

- `operation:parcel.collect` reads `entity:locker`

### Functional changes

- `workflow:deposit-parcel` `$.steps[id=s2].plain`

### Risk-relevant changes

- auth: `operation:parcel.deposit` `$.auth.authentication`

```mermaid
flowchart LR
  n0["operation: Collect parcel"]
  n1["operation: Deposit parcel"]
  n2["role: Recipient"]
  n3["workflow: Deposit a parcel"]
  n4["channel: Parcel outbox"]
  n5["component: Locker API"]
  n6["decision: Assign lockers with SKIP LOCKED"]
  n7["entity: Locker"]
  n8["entity: Parcel"]
  n9["entity: Parcel stored event"]
  n10["invariant: One parcel per locker"]
  n11["operation: Notify recipient"]
  n12["role: Courier"]
  n13["rule: Collection window"]
  n6 -->|"affects"| n1
  n8 -->|"lineage"| n0
  n8 -->|"lineage"| n1
  n9 -->|"lineage"| n1
  n10 -->|"enforced_at"| n1
  n0 -->|"child_of"| n5
  n0 -->|"reads"| n7
  n0 -->|"writes"| n7
  n0 -->|"reads"| n8
  n0 -->|"refers_to"| n8
  n0 -->|"writes"| n8
  n0 -->|"applies"| n13
  n1 -->|"publishes"| n4
  n1 -->|"child_of"| n5
  n1 -->|"refers_to"| n7
  n1 -->|"writes"| n7
  n1 -->|"refers_to"| n8
  n1 -->|"writes"| n8
  n1 -->|"enforces"| n10
  n1 -->|"authorizes"| n12
  n3 -->|"refers_to"| n8
  n3 -->|"has_step"| n1
  n3 -->|"has_step"| n11
  n3 -->|"refers_to"| n12
  class n2 added
  class n0,n1,n3 modified
  classDef added stroke:#2da44e,stroke-width:3px
  classDef removed stroke:#cf222e,stroke-width:3px,stroke-dasharray:4
  classDef modified stroke:#bf8700,stroke-width:3px
```
