/* i18n — nurses and CHWs are the primary users, in their own language
 * (product spec principle 6: Hausa, Yoruba, Igbo, Pidgin, English).
 * Keys are flat; t(key) falls back to English then to the key itself.
 */

export const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "pcm", name: "Naija (Pidgin)" },
  { code: "ha", name: "Hausa" },
  { code: "yo", name: "Yorùbá" },
  { code: "ig", name: "Igbo" },
];

const STRINGS = {
  en: {
    app_name: "OpenHealth", tasks: "Tasks", patients: "Patients", sync: "Sync", dashboard: "Dashboard", more: "More",
    due_today: "Due today", overdue: "Overdue", upcoming: "Upcoming", done: "Done", no_tasks: "No tasks — all caught up",
    register_patient: "Register patient", name: "Full name", age: "Age", village: "Village", phone: "Phone (optional)",
    pregnant: "Pregnant", save: "Save", cancel: "Cancel", search: "Search patients…",
    new_encounter: "New encounter", danger_signs: "Danger signs", none: "None", submit: "Record encounter",
    refer_emergency: "REFER NOW — emergency", routine: "Routine care", sync_now: "Sync now", last_sync: "Last sync",
    never: "Never", pending_docs: "Records waiting", pending_events: "Events queued", online: "Online", offline: "Offline — working locally",
    synced: "Everything synced", sync_failed: "Sync unreachable — will retry", settings: "Settings", language: "Language",
    facility: "Facility", about: "About", encounters_today: "Encounters today", tasks_due: "Tasks due",
    patients_registered: "Patients registered", anc_visit: "ANC visit", immunization: "Immunization", danger_followup: "Danger-sign follow-up",
    task_done_toast: "Task completed", patient_saved: "Patient saved — will sync when online", encounter_saved: "Encounter recorded",
    priority_high: "Urgent", priority_med: "Soon", priority_low: "Routine", version: "Version",
  },
  pcm: {
    app_name: "OpenHealth", tasks: "Work wey remain", patients: "Patients", sync: "Sync", dashboard: "Dashboard", more: "More",
    due_today: "Due today", overdue: "Don pass time", upcoming: "Wey dey come", done: "Done", no_tasks: "No work remain — everything done",
    register_patient: "Register patient", name: "Full name", age: "Age", village: "Village", phone: "Phone (if dem get)",
    pregnant: "Get belle", save: "Save", cancel: "Cancel", search: "Find patient…",
    new_encounter: "New visit", danger_signs: "Danger signs", none: "None", submit: "Record am",
    refer_emergency: "REFER NOW — emergency", routine: "Normal care", sync_now: "Sync now", last_sync: "Last sync",
    never: "Never", pending_docs: "Records wey dey wait", pending_events: "Events wey dey queue", online: "Online", offline: "No network — work dey go on",
    synced: "Everything don sync", sync_failed: "Sync no reach — e go retry", settings: "Settings", language: "Language",
    facility: "Facility", about: "About", encounters_today: "Visits today", tasks_due: "Work wey due",
    patients_registered: "Patients wey don register", anc_visit: "ANC visit", immunization: "Immunization", danger_followup: "Danger follow-up",
    task_done_toast: "Work done", patient_saved: "Patient saved — go sync when network come", encounter_saved: "Visit recorded",
    priority_high: "Urgent", priority_med: "Soon", priority_low: "Normal", version: "Version",
  },
  ha: {
    app_name: "OpenHealth", tasks: "Ayyuka", patients: "Marasa lafiya", sync: "Aiki", dashboard: "Allon bayani", more: "Kara",
    due_today: "Yau", overdue: "Ya wuce", upcoming: "Mai zuwa", done: "An gama", no_tasks: "Babu aiki — an gama duka",
    register_patient: "Rajista majinyaci", name: "Cikakken suna", age: "Shekaru", village: "Kauye", phone: "Wayar (ina akwai)",
    pregnant: "Mata da ciki", save: "Ajiye", cancel: "Soke", search: "Nemo majinyaci…",
    new_encounter: "Sabuwar ziyara", danger_signs: "Alamun hatsari", none: "Babu", submit: "Rubuta ziyara",
    refer_emergency: "TURA YANZU — gaggawa", routine: "Kula na yau da kullum", sync_now: "Aika yanzu", last_sync: "A karshe",
    never: "Taba", pending_docs: "Bayanai masu jira", pending_events: "Abubuwa a kan layi", online: "A layi", offline: "Babu network — aiki yana ci gaba",
    synced: "Duka sun tafi", sync_failed: "Aiki ya kasa — za a sake gwadawa", settings: "Saiti", language: "Harshe",
    facility: "Asibiti", about: "Game da", encounters_today: "Ziyarori yau", tasks_due: "Ayyukan da suka yi",
    patients_registered: "Marasa lafiya da aka rajista", anc_visit: "Ziyarar ANC", immunization: "Rigakafi", danger_followup: "Bin diddigon hatsari",
    task_done_toast: "An gama aiki", patient_saved: "An ajiye majinyaci — za a aika idan network ya dawo", encounter_saved: "An rubuta ziyara",
    priority_high: "Gaggawa", priority_med: "Nan ba da jimawa", priority_low: "Na yau da kullum", version: "Sigar",
  },
  yo: {
    app_name: "OpenHealth", tasks: "Àwọn iṣẹ́", patients: "Àwọn aláìsàn", sync: "Ìbámu", dashboard: "Pátáko ìwòye", more: "Ẹ̀kúnrẹ́rẹ́",
    due_today: "Ó yẹ lónìí", overdue: "Ó ti kọjá", upcoming: "Ńbọ̀", done: "Ti parí", no_tasks: "Kò sí iṣẹ́ mìíràn — gbogbo rẹ̀ ti parí",
    register_patient: "Forúkọsilẹ̀ aláìsàn", name: "Orúkọ kíkún", age: "Ọjọ́ orí", village: "Ìlú", phone: "Fóònù (tí ó bá sí)",
    pregnant: "Ńlóyún", save: "Fipamọ́", cancel: "Fagilee", search: "Wá aláìsàn…",
    new_encounter: "Àbúdá ọjọ́ tuntun", danger_signs: "Àmì ewu", none: "Kò sí", submit: "Gba ìsílẹ̀",
    refer_emergency: "RÁNDÁN SÍBÒ — pajawiri", routine: "Ìtọ́jọ́ déédéé", sync_now: "Ṣe ìbámu báyìí", last_sync: "Ìbámu gbèyìn",
    never: "Kò ṣe é rí", pending_docs: "Àwọn ìwé tí Ń dúró", pending_events: "Àwọn ìṣẹ̀lẹ̀ lórí ìlà", online: "Lórí ayélujára", offline: "Kò sí nẹ́tíwọ́ọ̀kì — iṣẹ́ Ń tẹ̀síwájú",
    synced: "Gbogbo rẹ̀ ti lọ", sync_failed: "Ìbámu kò dé — á tún gbìyànjú", settings: "Ètò", language: "Èdè",
    facility: "Ilé ìwòsàn", about: "Nípa", encounters_today: "Àwọn àbúdá òní", tasks_due: "Iṣẹ́ tó yẹ",
    patients_registered: "Aláìsàn tí a forúkọsilẹ̀", anc_visit: "Àbúdá ANC", immunization: "Ajẹsára", danger_followup: "Ìtọpasẹ̀ àmì ewu",
    task_done_toast: "Iṣẹ́ ti parí", patient_saved: "A ti fipamọ́ aláìsàn — á ṣe ìbámu tí ayélujára bá padà", encounter_saved: "A ti gba àbúdá sílẹ̀",
    priority_high: "Pajawiri", priority_med: "Láìpẹ́", priority_low: "Déédéé", version: "Ẹ̀yà",
  },
  ig: {
    app_name: "OpenHealth", tasks: "Ọrụ", patients: "Ndị ọrịa", sync: "Sịnc", dashboard: "Dashboard", more: "Ọzọ",
    due_today: "Taa", overdue: "Agafela", upcoming: "Na-abịa", done: "Emechara", no_tasks: "Ọ dịghị ọrụ fọrọ — emechaala ihe niile",
    register_patient: "Debanye ọrịa", name: "Aha zuru ezu", age: "Afọ", village: "Obodo", phone: "Ekwentị (ọ bụrụ na ọ dị)",
    pregnant: "Dị ime", save: "Chekwaa", cancel: "Kagbuo", search: "Chọọ ọrịa…",
    new_encounter: "Nleta ọhụrụ", danger_signs: "Ihe egwu", none: "Ọ dịghị", submit: "Dekọọ nleta",
    refer_emergency: "ZIGA UGBU A — mberede", routine: "Nlekọta nkịtị", sync_now: "Sịnc ugbu a", last_sync: "Sịnc ikpeazụ",
    never: "Abụọ", pending_docs: "Ndị na-eche", pending_events: "Ndị n'ahịrị", online: "N'ịntanetị", offline: "Enweghị netwọk — ọrụ na-aga n'ihu",
    synced: "Ihe niile ezigola", sync_failed: "Sịnc adịghị — ọ ga-anwa ọzọ", settings: "Ntọala", language: "Asụsụ",
    facility: "Ụlọ ọgwụ", about: "Banyere", encounters_today: "Nleta taa", tasks_due: "Ọrụ ruru",
    patients_registered: "Ndị ọrịa edebanyere", anc_visit: "Nleta ANC", immunization: "Mgbochi ọrịa", danger_followup: "Nsochi ihe egwu",
    task_done_toast: "Ọrụ emechara", patient_saved: "Echekwala ọrịa — ọ ga-ezigachaa mgbe netwọk laghachiri", encounter_saved: "Edetoola nleta",
    priority_high: "Mberede", priority_med: "N'oge na-adịghị anya", priority_low: "Nkịtị", version: "Ụdị",
  },
};

let current = "en";

export function setLanguage(code) {
  current = STRINGS[code] ? code : "en";
}

export function getLanguage() {
  return current;
}

export function t(key) {
  return STRINGS[current]?.[key] ?? STRINGS.en[key] ?? key;
}
