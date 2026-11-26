# Judge calibration: claude-haiku-5-5

**Agreement with human labels: 19/20 = 95%** (target at least 90%: MET)

- on cases a human marked faithful: 9/10
- on cases a human marked unfaithful: 10/10

| case | human | judge | agree | judge's reason |
|---|---|---|---|---|
| c01 | pass | pass | yes | 2/2 claims supported |
| c02 | fail | fail | yes | 0/1 claims supported; unsupported: ['Refunds are available for 90 days on all plans.'] |
| c03 | pass | pass | yes | 2/2 claims supported |
| c04 | fail | fail | yes | 1/2 claims supported; unsupported: ['The Team plan has unlimited seats.'] |
| c05 | pass | pass | yes | 1/1 claims supported |
| c06 | fail | fail | yes | 0/2 claims supported; unsupported: ['Business plan customers get a reply within 1 hour.', 'Business plan custo |
| c07 | pass | pass | yes | 2/2 claims supported |
| c08 | fail | fail | yes | 1/2 claims supported; unsupported: ['Your data is stored in Virginia.'] |
| c09 | pass | pass | yes | 1/1 claims supported |
| c10 | fail | fail | yes | 0/1 claims supported; unsupported: ['SAML single sign-on is included in every paid plan.'] |
| c11 | pass | pass | yes | 2/2 claims supported |
| c12 | fail | fail | yes | 1/3 claims supported; unsupported: ['The app works on iOS 14 and later.', 'The app has a Windows Phone version |
| c13 | pass | fail | **NO** | 1/2 claims supported; unsupported: ['Invoices are due 14 days after issue.'] |
| c14 | fail | fail | yes | 0/2 claims supported; unsupported: ['Invoices are issued on the 15th of each month.', 'Invoices are due within |
| c15 | pass | pass | yes | 3/3 claims supported |
| c16 | fail | fail | yes | 2/3 claims supported; unsupported: ['Exports can be done as PDF.'] |
| c17 | pass | pass | yes | 3/3 claims supported |
| c18 | fail | fail | yes | 1/3 claims supported; unsupported: ['Inactive accounts are deleted after 12 months.', 'Inactive accounts are d |
| c19 | pass | pass | yes | 2/2 claims supported |
| c20 | fail | fail | yes | 0/1 claims supported; unsupported: ['Failed webhooks are retried every minute until they succeed.'] |

Labels were written by hand for this repository (20 short synthetic cases), so this measures agreement on easy, clear-cut examples and should not be read as accuracy on your own data.
