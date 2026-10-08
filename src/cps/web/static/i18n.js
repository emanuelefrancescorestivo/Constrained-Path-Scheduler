// The scripts' own words, in the page's language (<html lang>, which the server
// sets from the plan). Everything else on a page is written by the server, from
// cps/locales/fr.py; tests/test_i18n.py checks both catalogues are complete.
const FR = {
  "Self-test": "Auto-test",
  "Deadline work": "Travail à rendre",
  "Exam practice": "Entraînement à l'examen",
  "Study": "Révision",
  "Done": "Fait",
  "Skipped": "Pas fait",
  "Hard": "Difficile",
  "The server answered {status}.": "Le serveur a répondu {status}.",
  "Courses": "Cours",
  "Busy": "Occupé",
  "Moved. It stays there; the rest of the plan has made room.": "Déplacée. Elle reste là ; le reste du plan s'est adapté.",
  "Saved. The plan has moved around it.": "Enregistré. Le plan s'est organisé autour.",
  "An exam from your timetable. Practice sessions are planned in the two weeks before it.":
    "Un examen de ton emploi du temps. Des séances d'entraînement sont prévues dans les deux semaines qui le précèdent.",
  "From your timetable. Nothing is planned at the same time.": "De ton emploi du temps. Rien n'est prévu en même temps.",
  "Placed by you": "Placée par toi",
  "How did it go?": "Comment ça s'est passé ?",
  "Recorded: {word}. The plan has adjusted.": "Noté : {word}. Le plan s'est adapté.",
  "Date": "Date",
  "Start": "Début",
  "Move it": "La déplacer",
  "It stays where you put it.": "Elle reste là où tu la mets.",
  "Or drag it on the calendar. It stays where you put it.": "Ou fais-la glisser dans le calendrier. Elle reste là où tu la mets.",
  "The plan chooses its time again.": "Le plan choisit de nouveau son horaire.",
  "Let the plan choose": "Laisser le plan choisir",
  "Move": "Déplacer",
  "Open this session's page": "Ouvrir la page de cette séance",
  "Name": "Nom",
  "From": "De",
  "To": "À",
  "Blocked. The plan has moved around it.": "Bloqué. Le plan s'est organisé autour.",
  "Block this time": "Bloquer ce créneau",
  "Busy time": "Créneau occupé",
  "What": "Quoi",
  "Kind": "Type",
  "Every {day}": "Chaque {day}",
  "Removed. That time is free again.": "Supprimé. Ce créneau est de nouveau libre.",
  "Delete": "Supprimer",
  "Cancel": "Annuler",
  "Block it": "Le bloquer",
  "Save": "Enregistrer",
  "{label}, {day}, {start} to {end}": "{label}, {day}, de {start} à {end}",
  "That did not work. Try again.": "Ça n'a pas marché. Réessaie.",
  "Done: {name}. Its remaining sessions are free again.": "Fait : {name}. Ses séances restantes sont de nouveau libres.",
  "Undo": "Annuler",
  "Saved.": "Enregistré.",
  "No connection to the server. Try again.": "Pas de connexion au serveur. Réessaie.",
  "Reopened: {name}.": "Rouvert : {name}.",
  "Could not reopen it.": "Impossible de le rouvrir.",
  "left the app {n} time ({minutes} min)": "app quittée {n} fois ({minutes} min)",
  "left the app {n} times ({minutes} min)": "app quittée {n} fois ({minutes} min)",
  "A photo could not be read. Try a JPEG or a PNG.": "Une photo n'a pas pu être lue. Essaie un JPEG ou un PNG.",
};

const CATALOGUES = { fr: FR };

export function t(text, values = {}) {
  const lang = (document.documentElement.lang || "en").slice(0, 2);
  const said = (CATALOGUES[lang] && CATALOGUES[lang][text]) || text;
  return said.replace(/\{(\w+)\}/g, (whole, key) => (key in values ? String(values[key]) : whole));
}

export const CATALOGUE_FR = FR;
