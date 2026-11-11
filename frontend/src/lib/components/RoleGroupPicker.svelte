<script>
  import { _ } from 'svelte-i18n';
  import { staged } from '../stores/filters.js';

  let { roleGroups = [], roleLabel = (id) => `Role ${id}` } = $props();

  // The staged role list is passed in explicitly so these helpers are pure
  // and the template shows what each binding depends on.
  function isChecked(id, roles) {
    return roles.includes(id);
  }

  function groupAllChecked(group, roles) {
    return group.roles.every(id => roles.includes(id));
  }

  function groupSomeChecked(group, roles) {
    return group.roles.some(id => roles.includes(id));
  }

  function toggleRole(id) {
    staged.update(s => {
      const roles = s.roles.includes(id)
        ? s.roles.filter(r => r !== id)
        : [...s.roles, id].sort((a, b) => a - b);
      return { ...s, roles };
    });
  }

  function toggleGroup(group) {
    staged.update(s => {
      const allOn = groupAllChecked(group, s.roles);
      const current = new Set(s.roles);
      for (const id of group.roles) {
        if (allOn) current.delete(id);
        else current.add(id);
      }
      return { ...s, roles: [...current].sort((a, b) => a - b) };
    });
  }
</script>

<div class="filter-section-title">{$_('filters.by_role')}</div>
<!-- reachable from the control it explains. Someone who does not
     know what "ISR" covers, or why a C-130 appears under four groups, is
     looking at exactly this list when the question occurs to them. -->
<p class="role-help">
  <a href="/roles.html" target="_blank" rel="noopener"
     data-breadcrumb="Opens a reference page listing every aircraft type code and the role groups it maps to, generated from the same configuration the pipeline uses.">
    How are aircraft assigned to roles?</a>
</p>
<div class="role-groups">
  {#each roleGroups as group}
    <div class="role-group">
      <div class="role-group-header">
        <input
          type="checkbox"
          class="group-check"
          checked={groupAllChecked(group, $staged.roles)}
          indeterminate={!groupAllChecked(group, $staged.roles) && groupSomeChecked(group, $staged.roles)}
          onchange={() => toggleGroup(group)}
        >
        <span class="group-label">{group.label}</span>
      </div>
      <div class="role-items">
        {#each group.roles as id}
          <div class="role-item">
            <input
              type="checkbox"
              checked={isChecked(id, $staged.roles)}
              data-ri={id}
              onchange={() => toggleRole(id)}
            >
            <span>{roleLabel(id)}</span>
          </div>
        {/each}
      </div>
    </div>
  {/each}
</div>

<style>
  .role-help { margin: 2px 0 8px; font-size: 11px; }
  .role-help a { color: #6ab4f0; text-decoration: none; }
  .role-help a:hover { text-decoration: underline; }
  /* .filter-section-title is styled globally in style.css, shared with
     DateRangePicker.svelte and TheaterToggle.svelte. */
  .role-group {
    margin-bottom: 8px;
  }
  .role-group-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
    cursor: pointer;
    user-select: none;
  }
  .group-label {
    font-size: 13px;
    color: #b0b8c4;
    font-weight: 500;
  }
  .role-items {
    padding-left: 22px;
  }
  .role-item {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 3px 0;
    font-size: 12px;
    color: #8a94a0;
  }
  input[type="checkbox"] {
    appearance: none;
    -webkit-appearance: none;
    width: 14px;
    height: 14px;
    flex-shrink: 0;
    background: #181c22;
    border: 1px solid #3a4050;
    border-radius: 3px;
    cursor: pointer;
    position: relative;
    transition: all 0.12s;
  }
  input[type="checkbox"]:checked {
    background: #1a5a9b;
    border-color: #3a8abb;
  }
  input[type="checkbox"]:checked::after {
    content: "";
    position: absolute;
    left: 4px;
    top: 1px;
    width: 4px;
    height: 8px;
    border: solid #e0eaf4;
    border-width: 0 2px 2px 0;
    transform: rotate(45deg);
  }
  input[type="checkbox"]:focus-visible {
    outline: 2px solid #4a8abb;
    outline-offset: 1px;
  }
</style>
