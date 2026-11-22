# Deadlock Tools 0.3.4

This patch fixes squadron menu corruption when adding or regrouping fighter and support slots.

- Reparent launch actions cleanly when rebuilding squadron submenus.
- Remove stale action references and retire old generated squadron menus left by earlier regrouping runs.
- Preserve unrelated custom submenus while regrouping launch actions.
- Give newly added launch actions a matching owning menu reference.

The project is licensed under the GNU General Public License, version 2 only (`GPL-2.0-only`).
