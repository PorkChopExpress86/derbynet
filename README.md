# Screenshots branch

This orphan branch holds the PR screenshots that the browser tests capture, one folder per plan ticket (`42/`, `43/`, ...).

Owner decision D75: the browser tests' screenshots satisfy a ticket's screenshot boxes once they are posted on the ticket. They are committed here and a ticket comment embeds them by raw commit URL, so they show inline and never expire (CI artifacts do expire).

This branch is never merged into `master`. It shares no history with it. Do not rebase or delete it: ticket comments link to files by commit SHA.
