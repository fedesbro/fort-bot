# Bot visivo – riavvio automatico partite

Il bot guarda lo schermo, riconosce le schermate fuori partita e fa i clic/tasti necessari per
ripetere il giro. Non tocca il codice del gioco.

## Il giro di una partita
1. **Lobby:** riconosce il bottone **gioca** e lo clicca → da quell'istante parte il **timer**,
   estratto a caso tra **10 e 14 minuti** (`match.seconds: [600, 840]`), nuovo a ogni partita.
2. **In partita** non si guarda nessuna schermata: conta solo il timer. Dopo il caricamento
   (`start_moving_after`) il personaggio corre/cammina **in tondo** (vedi sotto).
3. **Tempo scaduto:** Esc → **menu a tendina** → clic → **scheda** "torna alla lobby" → clic.
   Se dopo l'Esc il menu non compare, riprova ogni `retry_every` secondi.
4. Di nuovo in lobby → si ricomincia, all'infinito (o fino a `max_matches`).

In qualsiasi momento, se sullo schermo compare **male1**, **male2** o **token**, arriva una
notifica sul telefono (ntfy).

Template da creare in `templates/`: `gioca.png`, `btn_menu.png`, `btn_torna_lobby.png`,
`male1.png`, `male2.png`, `token.png`. All'avvio il bot controlla che ci siano tutti.

## File
| File | A cosa serve |
|---|---|
| `bot.py` | il bot |
| `input_titan.py` + `titan/` | tasti e mouse tramite il driver TitanCSA (unico canale di input) |
| `umano.py` | tempi e movimenti umani, la "mano" che gira la visuale |
| `config.yaml` | **la sequenza** e tutte le regolazioni (è l'unico file da modificare) |
| `calibra_giro.py` | taratura dei conteggi del mouse per un giro completo della visuale |
| `notifiche.py` | notifiche sul telefono tramite ntfy |
| `cattura_template.py` | ritaglia un bottone/scritta dallo schermo e lo salva in `templates/` |
| `prova_template.py` | controlla quanto bene ogni template viene riconosciuto |
| `installa.bat` / `avvia_bot.bat` | installazione e avvio con doppio clic |
| `logs/` | log di ogni sessione |
| `debug/` | screenshot salvati automaticamente quando qualcosa va storto |

## 0. Driver Titan Interceptor (una volta sola)
Tutti gli input passano **solo** dal driver **TitanCSA Interceptor-Keyboard-Mouse**
(https://github.com/TitanCSA/Interceptor-Keyboard-Mouse): tasti come codici di scansione e mouse come
spostamenti relativi. L'SDK è in C++, quindi il bot parla con un piccolo programma
`titan/titan_helper.exe` che tiene aperto il driver.

1. Scarica `InterceptorSetup.exe` dalle release del repo, lancialo, scegli "Install / reinstall",
   riavvia se richiesto e riapri il setup per completare le associazioni dei dispositivi.
2. Compila l'helper (una volta; servono `git` e Visual Studio Build Tools con C++):
   `powershell -ExecutionPolicy Bypass -File titan\build.ps1`
3. `installa.bat` (o `pip install -r requirements.txt`).
4. **Avvia il bot da un terminale come amministratore** (il driver lo richiede).

Attenzione: l'edizione demo funziona **1 ora per ogni avvio di Windows** (si rinnova riavviando il PC).

Senza driver il bot si ferma e lo segnala; `--dry-run` funziona anche senza.

Nella sezione `titan` di `config.yaml`: `keyboard` / `mouse` = id del dispositivo secondo il driver
(`null` = il primo trovato), `cursor_mode` (`relative` consigliato) e alias dei tasti.

## 1. Creare i template
```
.venv\Scripts\activate
python cattura_template.py gioca
```
Hai 3 secondi per andare sul gioco; poi trascina un rettangolo e premi **INVIO**.
Oppure da uno screenshot: `python cattura_template.py gioca --da-file screenshot_lobby.png`

- Ritaglia scritte o icone **fisse**, non zone animate o sfondi; 60–300 px di lato.
- Cattura alla stessa risoluzione con cui farai girare il bot.

Verifica con il gioco sulla schermata giusta: `python prova_template.py --mostra`
(il template giusto deve stare sopra la soglia dell'85%, gli altri ben sotto).

## 2. Tarare il cerchio (max 6 m)
A inizio partita il bot fissa il centro del cerchio; poi, sapendo quanto ha girato la visuale e
a che velocità va il personaggio, stima dove si trova e corregge le passate della mano per
restare sul cerchio (come chi guarda dove sta andando). Gli servono tre numeri del tuo gioco
(sezione `movement`):
1. `mouse_counts_per_360`: in partita, fermo, guarda un punto e lancia
   `python calibra_giro.py` (o `python calibra_giro.py 2400` per provare un valore).
   Se la visuale torna esattamente sul punto il valore è giusto; se si ferma prima alzalo,
   se lo supera abbassalo. **Se cambi la sensibilità nel gioco va rifatto.**
2. `run_speed` e `walk_speed`: velocità del personaggio in m/s (corsa con Shift, camminata).

Diametro nominale = `max_diameter × radius_fill` (6 × [0.6, 0.74] → tra 3,6 e 4,4 m).
In simulazione (12 minuti, con corsa, camminata, salti e soste) il personaggio resta dentro
~4,7 m con i valori tarati; se la velocità reale è sbagliata del 10-20% arriva a ~6 m.
Più la taratura è precisa, più il cerchio resta fermo sulla mappa.

## 3. Avviare
```
python bot.py --dry-run     # prova: riconosce e scrive nel log, ma NON clicca
python bot.py               # funzionamento normale
python bot.py --debug       # mostra i punteggi a ogni controllo
```
- **F8** pausa / riprendi · **F9** stop
- **Emergenza:** mouse nell'angolo in alto a sinistra dello schermo.

## Input umani
Con `human: enabled: true`:
- **tempi** (reazione, durata di tasti e clic, pause tra i tasti) estratti con una distribuzione
  asimmetrica come quella umana: di solito vicini al valore tipico, ogni tanto più lenti;
- **mouse nei menu:** solo spostamenti relativi, inviati a 500 report al secondo (`mouse_hz`)
  come un mouse vero, lungo una traiettoria curva con velocità a campana; a volte supera il
  bottone e torna indietro; gli ultimi pixel vengono "centrati" con piccole correzioni; il punto
  di clic cambia sempre e a volte il mouse si sposta di un pixel mentre il tasto è premuto;
- **visuale in corsa:** la "mano" gira il mouse a **passate** (circa mezzo secondo ciascuna,
  velocità a campana, picco mai nello stesso punto), con brevi pause tra una passata e l'altra
  e ogni tanto una pausa più lunga per riprendere la presa. Ogni passata sale o scende un poco
  e fa un piccolo arco; la deriva verticale viene riportata verso l'orizzonte nelle passate
  successive. C'è il tremore fisiologico della mano (8–11 Hz) e la velocità varia lentamente;
  se una pausa fa restare indietro, la passata dopo è un po' più ampia, così il cerchio resta tale;
- **tastiera in corsa:** W resta premuto per tutta la partita; l'andatura cambia premendo o
  lasciando Shift (tratti di corsa e di camminata di durata casuale). Shift e W vengono premuti
  con un piccolo sfasamento e in ordine variabile; ogni tanto un rilascio brevissimo di un tasto
  (dito che si riassesta), salti casuali, rare soste di mezzo secondo; rilascio finale in ordine
  sparso. Su un cerchio stretto le passate della mano si accorciano e le pause quasi spariscono,
  come succede a chi gira davvero così stretto.

`enabled: false` = movimenti istantanei e tempi esatti (solo per prove).

## Notifiche sul telefono
Scegli un nome di topic difficile da indovinare e scrivilo in `ntfy_topic.txt` (una riga, file escluso da git)
oppure in `notifications.topic` di `config.yaml`, o nella variabile `NTFY_TOPIC`.
Nell'app **ntfy** iscriviti allo stesso topic. Prova con `python notifiche.py`.

| Evento | Quando |
|---|---|
| `alert` | compare **male1**, **male2** o **token** (una notifica per comparsa, `alerts.cooldown`) |
| `start` | il bot è partito |
| `match_end` | fine di ogni partita, con la durata |
| `problem` | recovery, uscita dalla partita che non riesce, errore che ferma il bot |
| `stop` | il bot si è fermato, con motivo e riepilogo delle partite |

Ogni evento si spegne in `notifications.events`. Il nome del topic funziona come una password.

## Note
- Durante la partita le azioni si interrompono appena scade il timer: i tasti vengono rilasciati
  e parte l'uscita verso la lobby.
- F8, F9 e il mouse nell'angolo interrompono anche una corsa in corso.
- In ogni caso di uscita (fine azioni, errore, stop) **tutti i tasti premuti vengono rilasciati**.
