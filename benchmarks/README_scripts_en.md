# benchmarks/

Scripts de coleta de dados para as quatro métricas definidas no TCC, Cap. 3,
"Métricas e procedimentos experimentais" (Seção 3.5), além do procedimento
estatístico de comparação da Seção 3.6 ("Análise estatística").

Esses scripts executam contra **um firmware por vez**, pela porta serial/USB
CDC. Para comparar o Firmware A e o Firmware B, você executa o mesmo script
duas vezes — uma vez por firmware/plataforma — marcando cada execução com
`--firmware-label`, e então envia os dois CSVs para `compare.py`.

## Configuração

```bash
pip install -r benchmarks/requirements.txt
```

`pyserial` é suficiente para executar `m1`/`m2`/`m3`/`m4` (coleta de dados).
`numpy` e `scipy` são necessários apenas para `stats.py`/`compare.py` (a
análise da Seção 3.6).

Esses scripts importam `tools/debug_firmware.py` deste repositório (implementação
do protocolo e decodificador do fluxo) — execute-os a partir da raiz do
repositório ou com `benchmarks/` no `sys.path` (eles adicionam `../tools`
sozinhos; não há configuração extra além de `cd` para o repositório).

## Premissas da bancada (Cap. 3, "Bancada experimental")

- Um gerador de forma de onda alimenta uma onda quadrada de referência
  (frequência conhecida) aos canais habilitados, em paralelo com o DAQ_REF,
  para M1/M2; M3 ainda precisa de um contador binário livre. Esses scripts
  capturam e validam; eles não geram o estímulo.
- Um host, uma porta, uma compilação do firmware por vez.

## O que está coberto versus o que é manual ou fora do escopo

| Métrica | Script | Totalmente automatizado? |
|---|---|---|
| M1 — taxa máxima sustentada | `m1_max_rate.py` | Sim: contagem de bordas em 1 s após o disparo versus a referência do DAQ_REF (veja abaixo). (Comparar o resultado do B com o teto teórico f_clk/C_iter exige C_iter do desmontagem do laço de amostragem — uma etapa manual e offline, não calculada aqui.) |
| M2 — jitter de amostragem | `m2_jitter.py` | Apenas a metade em banda (distribuição dos períodos a partir das amostras decodificadas). A verificação cruzada no osciloscópio de um pino de depuração alternado pelo firmware é uma etapa manual separada da bancada — o firmware não possui tal instrumentação de pino de depuração. |
| M3 — perda em captura contínua | `m3_continuous_loss.py` | Sim, por meio de lacunas na sequência de contadores decodificada. O firmware atualmente não reporta um contador de overflow pela interface, então o overflow é inferido a partir de lacunas/abortos, e não por um campo dedicado. |
| M4 — ocupação de CPU | `m4_cpu_occupancy.py` | Sim, mas **exige firmware compilado com a instrumentação de ocupação DWT** (`src/occupancy.c`, `handle_get_cpu_occupancy.c`, o comando `'o'`). Firmware sem isso fará o script falhar rapidamente com erro claro. |

## Uso

Cada script percorre `--channels` (padrão `1,8,12`, correspondendo ao
`CHANNEL_SWEEP` do Cap. 3) e um sweep de taxa em escala logarítmica dentro da
faixa suportada pelo firmware, com `N_REPETITIONS = 30` repetições por ponto
por padrão (Cap. 3, "N=30 repetições").

```bash
# Firmware A (branch principal): grave-o e então
python3 benchmarks/m1_max_rate.py --firmware-label A --port /dev/ttyACM0 \
    --daq-ref-csv digital.csv --t-trigger 0
python3 benchmarks/m2_jitter.py   --firmware-label A --square-channel 0
python3 benchmarks/m3_continuous_loss.py --firmware-label A --duration 60
python3 benchmarks/m4_cpu_occupancy.py   --firmware-label A

# Firmware B (branch ana-gpio-cpu): grave-o e repita com rótulo B
python3 benchmarks/m1_max_rate.py --firmware-label B --port /dev/ttyACM0 \
    --daq-ref-csv digital.csv --t-trigger 0
...

# Variantes do Firmware B (dual-core / XIP-vs-SRAM / interrupts-on-off): mesmos
# scripts, uma recompilação por variante, rótulos distintos, por exemplo:
python3 benchmarks/m2_jitter.py --firmware-label B-sram
python3 benchmarks/m2_jitter.py --firmware-label B-xip
```

Os resultados são gravados em `benchmarks/results/m{1,2,3,4}_<label>.csv`
(`m1_<label>_edges.csv` para M1, `m2_<label>_trig.csv` para o M2 disparado)
(append-only — repetir um script adiciona mais repetições em vez de sobrescrever).

## Verificação cruzada com o DAQ_REF (1 s após o disparo)

O DAQ e o DAQ_REF armam o mesmo disparo de borda de subida na mesma onda
quadrada (gerador → ambos os canais); armam ambos e depois acionam o gerador.

- DAQ: `m2_jitter.py` arma um disparo de borda de subida em `--square-channel`
  (pretrigger 0) e mantém `--window` segundos (padrão 1) após ele. Os
  resultados ficam em `results/m2_<label>_trig.csv` (adicionando `window_s`,
  `n_samples`, `delivered_fraction`, `first_edge_sample`). Janelas até 1 MS
  (taxa ≤ 1 MS/s) usam uma captura fixa; as maiores usam modo contínuo e são
  recortadas para `rate * window` amostras. `delivered_fraction < 1` significa
  que o DAQ não entregou a janela completa. Se o firmware espera novamente o
  disparo em cada rearm contínuo (bloco de 1 MS) não é verificado — confira
  `delivered_fraction`.
- DAQ_REF, automatizado (`--daq-ref-live` em `m1_max_rate.py` / `m2_jitter.py`):
  com o Logic 2 aberto e *Preferences → Enable scripting socket server* ativado
  (porta 10430), cada repetição arma o DAQ **e** o DAQ_REF no mesmo disparo de
  borda de subida, e ambos aguardam o gerador. Inicie o script, ligue o
  gerador de funções e deixe-o ligado: o script percorre o sweep, toma uma
  referência do DAQ_REF por repetição (`results/ref/digital.csv`, disparo em
  t=0) e avalia ao final. M1 compara a contagem de bordas de cada repetição com o
  valor do DAQ_REF naquela repetição; M2 adiciona `ref_t_ns`, `ref_n_periods`,
  `fs_real_hz`, `rate_error_pct`, `period_count_diff` a cada linha. M1 realiza uma
  captura extra do DAQ_REF na inicialização para aprender a frequência do sinal
  (o gerador deve já estar ligado). `--trigger-timeout` (padrão 300 s) é o
  tempo que cada captura espera pelo gerador. Modo one-shot: `daq_ref.py --live`.
  Requer `logic2-automation`.
  Passe `--daq-ref-launch ~/Downloads/Logic-2.4.44-linux-x64.AppImage` para
  permitir que o script inicie o Logic 2 sozinho (`--no-sandbox --disable-gpu
  --automation`, log em `results/ref/logic2.log`) e o reinicie se travar: a
  repetição em andamento é então refeita em ambos os instrumentos, de modo que
  uma falha do Logic 2 nunca conta como falha do DAQ.
- DAQ_REF, manual: exporte a tabela digital (Logic 2 → Export Data → CSV) e então
  execute `python3 benchmarks/daq_ref.py digital.csv --channel 0 --m2-csv results/m2_B_trig.csv`.
  Apenas a borda de disparo → disparo + `--window` s é usada (disparo = primeira
  borda de subida de `--trigger-channel`, padrão `--channel`; ou `--t-trigger`).
  Mantenha `--window` igual dos dois lados. O CSV calibrado recebe `fs_real_hz`,
  `rate_error_pct`, `n_periods_ref` e `period_count_diff` (DAQ − períodos do DAQ_REF
  na janela).

### Critério de aprovação do M1

Mesmo sinal em todos os canais em teste e no DAQ_REF. Por repetição (disparo + 1 s):
janela entregue integralmente sem erros de decodificação; cada contagem de bordas
em cada canal dentro de `--tolerance-pct` (0,1%) da contagem do DAQ_REF (borda de
lançamento excluída: ela é a amostra 0 do DAQ); todos os canais iguais dentro de
±1 borda. Taxas abaixo de 4 amostras por período são ignoradas. Use
`--signal-channels` quando a onda chega apenas a alguns canais (por exemplo, `11`).
`reason` no CSV informa por que uma repetição falhou; `count_error_ppm` mostra o
erro real.

## Comparando A vs B (Cap. 3, Seção 3.6)

```bash
python3 benchmarks/compare.py results/m4_A.csv results/m4_B.csv \
    --value-column occupancy_fraction
```

`--value-column` escolhe a coluna a ser comparada por condição
(`(channels, rate_hz)`): `occupancy_fraction` para M4,
`period_mean_ns`/`period_std_ns`/`period_pkpk_ns` para M2,
`effective_rate_hz`/`gap_samples_total` para M3, `ok` para M1 (embora o
pass/fail do M1 normalmente seja lido diretamente no próprio CSV em vez de ser
comparado estatisticamente — um valor máximo fixo de taxa não tem distribuição
para testar; as repetições principalmente protegem contra uma taxa que falha
apenas às vezes).

Aplica, em uma única chamada, teste Mann-Whitney U (sempre, não paramétrico — sem
assumir normalidade), tamanho de efeito rank-biserial, correção de
Holm-Bonferroni em todas as condições comparadas naquela chamada (uma métrica =
uma família) e intervalo de confiança bootstrap de 95% (10.000 reamostragens)
para a diferença das medianas.

## Limite de taxa do lado do dispositivo (Firmware B)

O Firmware B mede o custo do laço de amostragem em boot
(`ana_module_calibrate()`) e recusa (sem ACK `'*'`) qualquer `R` acima da taxa
que o laço consegue sustentar para os canais habilitados, em vez de amostrar
silenciosamente mais lento do que solicitado. `setup_channels()` portanto habilita
os canais antes de ajustar a taxa (ordem dos drivers A, D, L, R) e retorna se a
taxa foi aceita; cada script interrompe o sweep para essa contagem de canais no
primeiro valor recusado.

## Observação conhecida de correção embutida nesses scripts

`tools/debug_firmware.py`'s `FirmwareDebugger.dig_bps` é derivado uma vez a partir
do número total de canais digitais do dispositivo (a string de identificação), o
que só coincide com o formato do fio quando todos os canais estão habilitados.
Esses benchmarks habilitam deliberadamente um **subconjunto** (1, 8 ou 12 dos
canais disponíveis), então calculam o `bytes_per_dig_sample` real do fio
(`common.digital_bytes_per_sample`, refletindo o `tx_init()` do firmware), em vez
de confiar em `dig_bps` — veja `common.capture_fixed_digital` /
`common.decode_continuous_stream`.
Capturas contínuas (`C`) também incorporam um marcador `"$<n>+"` uma vez por bloco
de 1.000.000 amostras (o limite do comando `'L'`), e não apenas uma vez ao final,
como em uma captura fixa (`F`) — `common.decode_continuous_stream` divide a cada
ocorrência antes de decodificar, em vez de decodificar o fluxo bruto inteiro de
uma única vez.
