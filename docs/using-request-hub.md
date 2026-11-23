# Using Request Hub

Start at **Dashboard**. What you can see and change depends on your role and the work assigned to you. Open **Notifications** for assignments, mentions, and release notes.

## Seasonal sign-in

When the Christmas sign-in theme is active, dark appearance adds falling snow and colored reflections from the bulbs. Drag the cord along the top or bottom of the card, or open **Christmas effects** to use **Nudge cord**. This menu also adjusts **Light flow** and pauses the seasonal animation. The pause setting stays in this browser. Below 480 px, snow remains but the cord and bulbs are hidden.

## Request workflow

1. **Submit.** Choose an account, engagement type, product category, priority, and requested start date. Request Hub assigns a reference code.
2. **Assign.** An Admin or PM-ESG assigns the primary engineer and, when needed, a backup. The SLA due date adds **5 working days** for Medium priority or **3** for High, skipping weekends.
3. **Deliver.** The primary engineer acknowledges the assignment and records progress. Current assignees can post updates within their permissions, and eligible engineers can log billable or non-billable hours.
4. **Close and review.** The primary engineer or an authorized management role marks the request complete. A request may have multiple linked Service Quotation Requests (SQRs). Linking a request is optional when filing an SQR.

### Updates and mentions

Type `@` or select **Mention** in Updates. Eligible recipients are the requestor, current primary and backup engineers, and active Admin or PM-ESG users. Use the arrow keys to select a name, Enter to insert it, and Escape to dismiss the list. Repeating the same mention does not send duplicate alerts.

## Role permissions

| Role | Access |
| --- | --- |
| **Requestor** | Create and track own requests, update eligible details, and post updates. |
| **Requestor-ESS** | Create and track own requests. These requests are visible in the PM-ESS dashboard scope. |
| **PM-ESS** | Create requests. **All Requests** shows Requestor-ESS records plus the PM’s own, not all company requests. **My Requests** shows only their own. |
| **PM-ESG** | Review, assign, and manage requests across the organization; access reports and exports; review SQRs assigned to them. PPT opens with **My Assigned** by default. |
| **Engineer** | View primary, backup, and SQR-linked requests. Current assignees can post updates; only the primary engineer can change status. Eligible engineers can log activity and file SQRs. |
| **On Hold** | Keep access to existing assignments, the personal activity graph, and engineer-scoped SQR filing and tracking. Cannot receive new assignments or add activity logs. |
| **Admin** | Manage requests, assignments, users, forms, and changelogs; access reporting and SQR review. |

Record-specific assignment and review rules still apply. The application’s **Admin** role is separate from Django staff and superuser status.

## Dashboards and reports

- **Dashboard:** Open a request from its reference code. Engineers can switch between primary and backup assignments.
- **Operational Reports:** Compare created and completed requests, see work needing attention, and review assignment trends.
- **Activity Logs Reports:** Review monthly hours, billable share, and work location. Available actions depend on your role.
- **Filters** shows the selected period even when collapsed. **Export CSV** uses that period. Select **Load more** for all results in a capped card and **View chart values** for exact values.

See [chart conventions](unreleased-chart-consistency-changelog.md#chart-conventions), [Reports details](unreleased-ppt-inspired-reports-changelog.md), and [responsive navigation](unreleased-responsive-navbar-changelog.md).

## Quotations and project performance

- **SQR Tracker** provides List and Table views of the same authorized records. Within a filter field, selected options are combined as alternatives; different fields must all match. For Engineers and On Hold users, **My Assigned** matches the saved Assigned SSE, not the record creator.
- From the tracker, choose **To Reports** to see quotation and revenue breakdowns. Choose **To SQR Tracker** to go back.
- Approved quotations can be downloaded as branded `.eml` files. If you choose to open an Outlook draft, paste the copied formatted message into the blank draft body.
- **Project Performance Tracking (PPT)** summarizes saved Schedule, Budget/Effort, and Quality assessments. Blank dimensions are **Not assessed**. Ratings are not inferred from dates or progress percentages.

See [tracker filters, delivery, and maintenance](unreleased-sqr-unified-tracker-changelog.md) and [PPT navigation and defaults](unreleased-ppt-navigation-changelog.md).

## Profile and administration

Use **Settings** to preview or apply an account theme. The light/dark control is separate. For authorized administrators, the [Form Editor guide](form-editor.md) covers form customization, and the [Admin Profile guide](unreleased-admin-profile-tools-changelog.md) covers Classic Tracker visibility and recovery options.
