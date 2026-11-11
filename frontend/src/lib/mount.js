import './i18n.js';
import { createClassComponent } from 'svelte/legacy';
import WhatIfDetail from './components/WhatIfDetail.svelte';
import Toolbar from './components/Toolbar.svelte';
import ReferenceViewModal from './components/ReferenceViewModal.svelte';
import InstagramShareModal from './components/InstagramShareModal.svelte';
import EmbedPopup from './components/EmbedPopup.svelte';
import ContentPanel from './components/ContentPanel.svelte';
import MobileToolbar from './components/MobileToolbar.svelte';
import OnMapLegend from './components/OnMapLegend.svelte';
import WhatIfPulldown from './components/WhatIfPulldown.svelte';

const mounts = new Map();

function mount(target, Component, props = {}) {
  unmount(target);
  const component = createClassComponent({ component: Component, target, props });
  mounts.set(target, component);
  return component;
}

export function mountWhatIfDetail(target, opts = {}) {
  return mount(target, WhatIfDetail, {
    resultsHtml: opts.resultsHtml || '',
    onResultsRendered: opts.onResultsRendered || null,
  });
}

export function mountWhatIfPulldown(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, WhatIfPulldown, {
    onExpand: opts.onExpand || null,
    onCollapse: opts.onCollapse || null,
    onDateChange: opts.onDateChange || null,
  });
}

export function mountToolbar(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, Toolbar, {
    socialButtons: opts.socialButtons || [],
    showNative: opts.showNative || false,
    showExplore: opts.showExplore || false,
    exploreActive: opts.exploreActive || false,
    onCopyLink: opts.onCopyLink || null,
    onEmbed: opts.onEmbed || null,
    onNativeShare: opts.onNativeShare || null,
    onMastodon: opts.onMastodon || null,
    onInstagram: opts.onInstagram || null,
    onSaveImage: opts.onSaveImage || null,
    onExportCsv: opts.onExportCsv || null,
    onRectExport: opts.onRectExport || null,
    onExploreToggle: opts.onExploreToggle || null,
    // The Filters trigger and the Show toggle live in the
    // toolbar's two semantic groups.
    onFilters: opts.onFilters || null,
    filtersActive: opts.filtersActive || false,
    filterBadge: opts.filterBadge || '',
    showFlights: opts.showFlights !== undefined ? opts.showFlights : true,
    showLandings: opts.showLandings || false,
    showDisabled: opts.showDisabled || false,
    onShowToggle: opts.onShowToggle || null,
  });
}

export function mountReferenceView(target) {
  target.innerHTML = '';
  return mount(target, ReferenceViewModal, { open: false });
}

export function mountInstagramModal(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, InstagramShareModal, {
    open: false,
    caption: '',
    onClose: opts.onClose || null,
  });
}

export function mountEmbedPopup(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, EmbedPopup, {
    open: opts.open || false,
    snippet: opts.snippet || '',
    onClose: opts.onClose || null,
  });
}

export function mountContentPanel(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, ContentPanel, {
    onSectionSelect: opts.onSectionSelect || null,
    onLayoutChange: opts.onLayoutChange || null,
    onSidebarFoldChange: opts.onSidebarFoldChange || null,
    onSelectCountry: opts.onSelectCountry || null,
    onDeselectCountry: opts.onDeselectCountry || null,
    onEnterRefusal: opts.onEnterRefusal || null,
    onExitRefusal: opts.onExitRefusal || null,
    footerText: opts.footerText || '',
    footerContact: opts.footerContact || null,
    footerTourLabel: opts.footerTourLabel || '',
    footerRepo: opts.footerRepo || null,
    getFilterPanelProps: opts.getFilterPanelProps || null,
  });
}

export function mountMobileToolbar(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, MobileToolbar, {
    socialButtons: opts.socialButtons || [],
    showNative: opts.showNative || false,
    onCopyLink: opts.onCopyLink || null,
    onEmbed: opts.onEmbed || null,
    onNativeShare: opts.onNativeShare || null,
    onMastodon: opts.onMastodon || null,
    onInstagram: opts.onInstagram || null,
    onFilters: opts.onFilters || null,
    filterBadge: opts.filterBadge || '',
    showFlights: opts.showFlights ?? true,
    showLandings: opts.showLandings ?? false,
    showDisabled: opts.showDisabled || false,
    onShowToggle: opts.onShowToggle || null,
  });
}

export function mountOnMapLegend(target, opts = {}) {
  target.innerHTML = '';
  return mount(target, OnMapLegend, {
    levels: opts.levels || [],
    altColors: opts.altColors || [],
    altBands: opts.altBands || 16,
    altBandKft: opts.altBandKft || 2.5,
    showFlights: opts.showFlights ?? true,
    showLandings: opts.showLandings ?? false,
    startCollapsed: opts.startCollapsed ?? false,
  });
}

export function updateProps(target, props) {
  const component = mounts.get(target);
  if (component) component.$set(props);
}

export function unmount(target) {
  const existing = mounts.get(target);
  if (existing) {
    existing.$destroy();
    mounts.delete(target);
  }
}
