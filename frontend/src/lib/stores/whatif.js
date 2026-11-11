import { writable } from 'svelte/store';

export const whatif = writable({
  eligible: false,
  active: false,
  expanded: false,
  denialDay: null,
  minDay: 0,
  maxDay: 0,
  countryName: '',
});
