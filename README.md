# francesco4ai.github.io

Sito personale, servito da GitHub Pages: HTML, CSS e JavaScript statici, senza build.

## Struttura

| Percorso | Cosa contiene |
| --- | --- |
| `index.html` | La pagina. Il blocco tra `<!-- ORCID:BEGIN -->` e `<!-- ORCID:END -->` è generato: non modificarlo a mano. |
| `assets/css/site.css`, `assets/js/site.js` | Stile (tema chiaro/scuro) e piccoli comportamenti: filtro delle pubblicazioni e cambio tema. |
| `data/publications.extra.json` | **L’unico file delle pubblicazioni da modificare a mano** (vedi sotto). |
| `data/orcid.json` | Cache dei lavori letti da ORCID (generato). |
| `data/publications.json`, `publications.bib` | Elenco finale in JSON e in BibTeX (generati). |
| `scripts/orcid_sync.py` | Lo script di sincronizzazione (solo libreria standard di Python). |
| `.github/workflows/deploy.yml` | Sincronizza da ORCID e pubblica il sito. |

## Come si aggiornano le pubblicazioni

Ogni giorno, e a ogni push su `main`, il workflow *Sync ORCID and deploy*:

1. legge i lavori pubblici del record ORCID `0009-0006-3474-8692` (per ogni gruppo di duplicati usa la versione indicata come preferita su ORCID);
2. completa autori, sede e acronimo della conferenza da Crossref quando c’è un DOI;
3. applica le annotazioni di `data/publications.extra.json`;
4. rigenera la sezione Publications, `publications.bib` e i JSON;
5. se qualcosa è cambiato fa un commit (“Update publications from ORCID”) e pubblica il sito.

Per aggiungere un articolo basta che sia su ORCID con visibilità **Everyone**: compare sul sito entro un giorno, con l’etichetta “New” per 90 giorni. Per non aspettare: *Actions → Sync ORCID and deploy → Run workflow*.

Se ORCID non risponde, il sito viene pubblicato comunque con l’ultimo elenco salvato e il run mostra un avviso.

## `data/publications.extra.json`

Serve per ciò che ORCID non sa. Ogni voce di `papers` viene associata a un lavoro ORCID tramite DOI o, in mancanza, per somiglianza del titolo.

```json
{
  "id": "adocl",
  "title": "ADoCL: Anomaly-Aware Dynamic Concept Maintenance for Reliable Continual Learning",
  "code": "https://github.com/AIDALab-DIMES/ADoCL",
  "award": "Top 5 best papers at SISAP 2026",
  "short": "SISAP 2026",
  "authors": ["F. Angiulli", "F. De Luca", "F. Fassetti"],
  "venue": "Similarity Search and Applications",
  "year": 2027
}
```

- **Annotazioni**, sempre applicate: `id` (ancora `#pub-<id>` usata nei link della pagina), `code`, `award`, `short`, `pdf`, `slides`, `poster`, `video`, `kind` (`journal`, `conference`, `preprint`, `thesis`, `other`).
- **Dati di riserva**: `authors`, `venue`, `year`, `month`, `doi`, `url`, `volume`, `issue`, `pages`, `publisher`, `series`. Vengono usati solo dove ORCID non ha il dato. Se l’articolo non è ancora su ORCID, la voce viene mostrata lo stesso, purché abbia almeno `title`, `authors`, `venue` e `year`.
- `"force": { "venue": "..." }` sovrascrive qualunque campo anche se ORCID lo fornisce.
- `"hide": true` nasconde un lavoro presente su ORCID.

Se una voce non viene associata al lavoro ORCID giusto (titoli molto diversi), aggiungi il campo `doi`.

## In locale

```bash
python3 scripts/orcid_sync.py            # legge ORCID e rigenera
python3 scripts/orcid_sync.py --offline  # rigenera solo da cache e annotazioni
python3 -m http.server                   # poi apri http://localhost:8000
```

## Note

- Nei repository pubblici GitHub sospende i workflow pianificati dopo 60 giorni senza attività. GitHub avvisa via email prima della sospensione: basta riattivarlo dalla scheda Actions.
- L’API pubblica di ORCID non richiede credenziali. Se vuoi limiti più alti, registra un client gratuito su ORCID e salva il token `/read-public` come secret `ORCID_TOKEN` del repository.
