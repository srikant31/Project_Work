import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { ManagedCategory } from '../types';
import { EmptyState, ErrorBanner, Notice, Spinner } from './Shared';

function sorted(categories: ManagedCategory[]) { return [...categories].sort((a, b) => a.category_name.localeCompare(b.category_name)); }

export default function Categories() {
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [editing, setEditing] = useState<ManagedCategory | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [removing, setRemoving] = useState<ManagedCategory | null>(null);
  useEffect(() => {
    let active = true; setLoading(true); setError(null);
    api.managedCategories().then(result => { if (active) setCategories(sorted(result.categories)); }).catch(caught => { if (active) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  function beginEdit(category: ManagedCategory | null) {
    setEditing(category); setName(category?.category_name || ''); setDescription(category?.description || ''); setShowForm(true); setRemoving(null); setError(null);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy || !name.trim()) return; setBusy(true); setError(null); setNotice(null);
    const target = editing;
    try {
      const result = target ? await api.updateCategory(target.category_id, name.trim(), description.trim() || null) : await api.createCategory(name.trim(), description.trim() || null);
      setCategories(current => sorted(target ? current.map(item => item.category_id === target.category_id ? result.category : item) : [...current, result.category]));
      setNotice(`Category “${result.category.category_name}” ${target ? 'updated' : 'created'}.`); setShowForm(false); setEditing(null);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!removing || busy) return; const target = removing; setBusy(true); setError(null); setNotice(null);
    try {
      await api.deleteCategory(target.category_id); setCategories(current => current.filter(item => item.category_id !== target.category_id));
      setNotice(`Unused category “${target.category_name}” deleted.`); setRemoving(null);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">WORKSPACE / CATEGORIES</span><h1>Categories<span className="heading-dot">.</span></h1><p>Maintain the categories available when uploading knowledge documents.</p></div><button className="button primary" disabled={busy || loading} onClick={() => beginEdit(null)}><Plus size={16} />Add category</button></div>
    <Notice message={notice} onClose={() => setNotice(null)} /><ErrorBanner message={error} />
    <p className="admin-explanation">Renaming keeps the same category identity and updates its displayed name on linked documents. Counts include all stored document versions. Only categories with no document references can be deleted.</p>
    {showForm && <section className="panel admin-section category-editor" aria-labelledby="category-editor-title"><div className="panel-heading"><div><h2 id="category-editor-title">{editing ? `Edit “${editing.category_name}”` : 'New category'}</h2><p>Names must be unique regardless of capitalization.</p></div><button className="icon-button" disabled={busy} aria-label="Close category form" onClick={() => setShowForm(false)}><X size={17} /></button></div><form onSubmit={save}><label htmlFor="category-name">Category name<input id="category-name" aria-label="Category name" value={name} maxLength={100} required disabled={busy} onChange={event => setName(event.target.value)} /></label><label htmlFor="category-description">Description <span className="optional">(optional)</span><textarea id="category-description" aria-label="Category description" rows={3} value={description} maxLength={1000} disabled={busy} onChange={event => setDescription(event.target.value)} /></label><div className="category-form-actions"><button className="button primary" type="submit" disabled={busy || !name.trim()}>{busy ? <Spinner label="Saving category…" /> : editing ? 'Save category' : 'Create category'}</button><button className="button secondary" type="button" disabled={busy} onClick={() => setShowForm(false)}>Cancel</button></div></form></section>}
    {removing && <section className="panel category-delete-confirm" role="region" aria-labelledby="delete-category-title"><h2 id="delete-category-title">Delete “{removing.category_name}”?</h2><p>This removes the unused category from future upload choices. The server checks again that no stored document version references it.</p><div className="category-form-actions"><button className="button danger" disabled={busy} onClick={remove}>{busy ? <Spinner label="Deleting category…" /> : 'Confirm delete category'}</button><button className="button secondary" disabled={busy} onClick={() => setRemoving(null)}>Cancel deletion</button></div></section>}
    <section className="panel admin-section"><div className="panel-heading"><div><h2>Document categories <span className="count-pill">{categories.length}</span></h2><p>Changes are recorded in Activity.</p></div><button className="icon-button" aria-label="Refresh categories" disabled={loading || busy} onClick={() => { setRemoving(null); setShowForm(false); setRefresh(value => value + 1); }}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>{loading ? <div className="loading-box"><Spinner label="Loading categories…" /></div> : categories.length ? <div className="category-list">{categories.map(category => <article key={category.category_id}><div><h3>{category.category_name}</h3><p>{category.description || 'No description.'}</p><small>{category.document_count} stored document {category.document_count === 1 ? 'version' : 'versions'}{category.document_count > 0 ? ' · In use; deletion unavailable' : ' · Unused'}</small></div><div className="category-actions"><button className="button secondary small" disabled={busy} onClick={() => beginEdit(category)} aria-label={`Edit category ${category.category_name}`}><Pencil size={13} />Edit</button><button className="icon-button" disabled={busy || category.document_count > 0} title={category.document_count > 0 ? 'Referenced by stored document versions' : `Delete unused category ${category.category_name}`} aria-label={`Delete category ${category.category_name}`} onClick={() => { setRemoving(category); setShowForm(false); setError(null); }}><Trash2 size={16} /></button></div></article>)}</div> : !error && <EmptyState title="No categories yet">Create a category to make it available for document uploads.</EmptyState>}</section>
  </>;
}
