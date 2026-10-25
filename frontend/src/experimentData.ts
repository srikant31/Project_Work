import type { GoldQuestion } from './types';

export function parseGoldQuestions(value: string): GoldQuestion[] {
  let parsed: unknown;
  try { parsed = JSON.parse(value.trim()); } catch { throw new Error('Question dataset must be valid JSON.'); }
  if (!Array.isArray(parsed) || !parsed.length) throw new Error('Provide a nonempty JSON array of gold questions.');
  if (parsed.length > 100) throw new Error('Use up to 100 questions per experiment.');
  const seen = new Set<string>();
  for (const item of parsed) {
    if (!item || typeof item !== 'object' || Array.isArray(item) || typeof item.question_id !== 'string' || !item.question_id.trim() || typeof item.question !== 'string' || !item.question.trim() || typeof item.answerable !== 'boolean') throw new Error('Every question needs a question_id, question text and boolean answerable label.');
    if (seen.has(item.question_id)) throw new Error(`Question ID ${item.question_id} appears more than once.`);
    seen.add(item.question_id);
  }
  return parsed as GoldQuestion[];
}

export function remapGoldDocumentIds(value: string, replacements: ReadonlyMap<string, string>): { dataset: string; changed: number; warning: string | null } {
  if (!replacements.size) return { dataset: value, changed: 0, warning: null };
  let questions: GoldQuestion[];
  try { questions = parseGoldQuestions(value); }
  catch {
    return { dataset: value, changed: 0, warning: 'The editable question draft could not be updated. It was kept unchanged. Review its JSON and update expected_doc_id labels to the new versions before running again.' };
  }
  let changed = 0;
  const updated = questions.map(question => {
    const replacement = question.expected_doc_id ? replacements.get(question.expected_doc_id) : undefined;
    if (!replacement || replacement === question.expected_doc_id) return question;
    changed += 1;
    return { ...question, expected_doc_id: replacement };
  });
  return { dataset: changed ? JSON.stringify(updated, null, 2) : value, changed, warning: null };
}
