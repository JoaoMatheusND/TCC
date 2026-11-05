# Divisão de slides — Defesa do TCC

> Base: `Monografia/` (Caps. 1–6). Números conferidos com o texto da monografia,
> **não** com o artigo do BRACIS (os resultados da Robust Fitness mudaram: no TCC ela
> usa G\*=36 gerações e o Robustness Predictor).
>
> Tempo: **25–30 min** de fala → 31 slides de conteúdo (~50 s cada) +
> capa, roteiro e 5 aberturas de seção (automáticas, não numeradas).
> Mira de ~27 min: Intro 3,5 · Fundamentação 3,5 · Metodologia 8 · Resultados 10 · Conclusão 2.
> Slides marcados com **[backup]** ficam depois do "Obrigado", para perguntas da banca.
>
> Fio condutor: **problema → diagnóstico → solução → evidência**.
> A pergunta que a apresentação responde: *a busca do OFA produz subredes robustas?
> Não. Dá pra corrigir mudando a fitness? Sim.*

---

## Abertura

| # | Slide | Conteúdo | Visual |
|---|---|---|---|
| — | Capa | Título, subtítulo, autor, orientador, IC/UFAL, data | template |
| — | Roteiro | 5 seções | `\tableofcontents` |

## 1. Introdução (~3,5 min)

| # | Slide | Conteúdo | Visual |
|---|---|---|---|
| 1 | **TinyML: IA no microcontrolador** | CNNs na borda: privacidade, latência, energia · SRAM/Flash ordens de grandeza menores que em smartphones · cada hardware exige uma arquitetura própria | 2–3 `\bignumber` (ex.: KB de SRAM de um MCU vs. GB de um smartphone) |
| 2 | **Once-for-All: treinar uma vez, extrair muitas** | Uma *supernet* treinada uma vez · subredes extraídas sob demanda, sem retreino · busca (evolutiva) escolhe a subrede para cada restrição de hardware | `Cap02/img/ofa.png` |
| 3 | **O problema: o mundo real não é limpo** | Modelos de visão perdem acurácia sob ruído, desfoque, clima · a busca do OFA otimiza **só acurácia limpa** · ninguém avaliou o OFA sob corrupções · dispositivos *always-on* operam sem supervisão | faixa com 4–5 recortes de `vwwc_corruption_grid.pdf` |
| 4 | **Hipótese e objetivo** | `\notebox` com a hipótese · `block` com o objetivo geral | texto |
| 5 | **Objetivos específicos / contribuições** | 4 itens numerados: VWW-C · diagnóstico da busca padrão · Robustness Predictor · Robust/Weighted Fitness | lista numerada (vira o "mapa" do resto da apresentação) |

## 2. Fundamentação e Trabalhos Relacionados (~3,5 min)

| # | Slide | Conteúdo | Visual |
|---|---|---|---|
| 6 | **Neural Architecture Search** | Espaço de busca · estratégia de busca · estimativa de desempenho · preditor substituto evita treinar cada candidata | `Cap02/img/NAS-methods.png` |
| 7 | **Busca evolutiva** | População → seleção (elitismo *top-K*) → mutação/cruzamento → nova geração · *fitness* decide quem sobrevive ← **é aqui que o trabalho atua** | diagrama TikZ circular simples |
| 8 | **Robustez a corrupções naturais** | ImageNet-C: 15 corrupções × 5 severidades · natural ≠ adversarial (não direcionada vs. construída para enganar) | 1 imagem do VWW-C em 3–4 corrupções × severidades 1→5 (evita apresentar o dataset da manga) |
| 9 | **Trabalhos relacionados** | Tabela de posicionamento: Xie 2017 (Genetic CNN), Jung 2023 (NAS-Bench-201 robustez), Andrade 2025 (manga), MCUNetV2/OFA, **este trabalho** × colunas: restrição de HW · corrupções naturais · robustez **dentro** da busca · preditor de robustez no laço | tabela com ✓/✗, última linha destacada |

## 3. Metodologia (~8 min)

| # | Slide | Conteúdo | Visual |
|---|---|---|---|
| 10 | **Visão geral** | Pipeline: VWW-C → busca baseline → diagnóstico → Robustness Predictor → estudo de gerações (G\*) → Robust/Weighted Fitness → reavaliação real | diagrama TikZ horizontal (reaparece pequeno no topo dos slides seguintes, com a etapa atual destacada) |
| 11 | **VWW → VWW-C** | VWW: 8.059 imagens, *Person* / *Not a Person* · metodologia ImageNet-C · 15 corrupções × 5 severidades = 75 subconjuntos · avaliação em severidade 5 (pior caso) · disponibilizado publicamente | `vwwc_corruption_grid.pdf` |
| 12 | **Espaço de busca: supernet MCUNetV2** | Pesos pré-treinados públicos (não treinados aqui) · 6 estágios, até 20 blocos MBConv · por bloco: kernel {3,5,7}, expansão {3,4,6} · por estágio: profundidade {0,1,2} · global: largura {0,5; 0,75; 1,0}, resolução 96–160 px · subrede = uma tupla → codificação *one-hot* para os preditores | diagrama TikZ de blocos com os "botões" variáveis |
| 13 | **Seis configurações de hardware** | 18M / 52M MACs (latência, energia) × 128 / 256 / 512 KB de memória de pico (SRAM) · do mais restrito ao mais folgado do espectro TinyML | tabela 2×3 + `hardware_configs_scatter.pdf` |
| 14 | **Busca baseline** | Restrição de HW = filtro rígido · fitness = acurácia limpa (AccuracyPredictor) · pop. 20, 10 gerações · 30 execuções × 6 configs = **180 subredes** | `evolutionary_cycle_flowchart.pdf` |
| 15 | **Como medimos robustez** | MCA = média das 15 acurácias corrompidas · Δc = Acc limpa − Acc corrompida · desvio-padrão entre execuções = estabilidade da busca | 3 equações reveladas uma a uma (`\pause`) |
| 16 | **Robustness Predictor** | Avaliar 15 corrupções por candidato a cada geração é inviável · 50 mil arquiteturas avaliadas uma vez · MLP 3×400 (mesma arquitetura do AccuracyPredictor), L1, Adam, 75 épocas · custo pago **uma vez**, não a cada busca | `robustness_predictor_architecture.pdf` |
| 17 | **Quantas gerações bastam?** | 10 gerações do baseline nunca foram justificadas · busca até convergir: paciência 10, teto 100 · 30 execuções × 6 configs · G\* = média das medianas | diagrama/linha do tempo simples |
| 18 | **Robustez na fitness** | F_R = Acc_clean + MCA (peso igual) · F_w = w·Acc_clean + (1−w)·MCA, w ∈ {0; 0,2; …; 1} (só 18M/128KB) · ambas com G\* e MCA estimada pelo preditor | 2 equações em `\notebox` |

## 4. Resultados (~10 min)

| # | Slide | Conteúdo | Visual |
|---|---|---|---|
| 19 | **Baseline: a robustez varia muito** | 180 subredes avaliadas de verdade no VWW-C · σ > 0,75 nas 3 configs de 18M · busca padrão às vezes escolhe subredes eficientes mas muito vulneráveis | `mca_boxplots_baseline.pdf` |
| 20 | **Baseline: onde dói mais** | Tabela: acc limpa, MCA, σ, pior/melhor corrupção por config · Contrast é a pior em todas · **−39,15 pp** sob Contrast justamente em 52M/512KB, a config mais folgada | tabela compacta + `\bignumber{−39,15 pp}` |
| 21 | **Robustness Predictor: treinamento** | 50 mil pares, 80/20 · menor MAE de validação **0,097%** na época 61 (de 75) | `robustness_predictor_training_curve.pdf` |
| 22 | **Robustness Predictor: funciona nas subredes buscadas?** | Amostra aleatória ≠ subredes que a busca escolhe → reavaliação real · MAE 0,118–0,247%, viés sempre positivo (superestima levemente) · maior erro em 52M/128 e 52M/256, ainda ≪ MCA típica (~70–74%) | `robustness_predictor_scatter.pdf` + tabela pequena (N, MAE, bias) |
| 23 | **Convergência por configuração** | Nenhuma das 180 buscas bateu o teto de 100 · 14 a 94 gerações · 52M precisa de mais (mediana até 48,5) · 52M/256: maior dispersão (σ 19,5) | tabela (média, mediana, σ, mín, máx) |
| 24 | **G\* = 36 gerações** | Retorno decrescente: convergir mais tarde não dá MCA muito maior · média das medianas (36,5) → **36**, mais que 3× as 10 do baseline | `generations_convergence_diagnostics.pdf` + `\bignumber{36}` |
| 25 | **Robust Fitness vs. baseline** | MCA ↑ em **6/6** configs (+1,36 a +2,94 pp) · σ ↓ em 4/6, até **−52%** (18M/512KB) · 52M/256 e 52M/512: σ sobe levemente (+0,04/+0,08) → leitura: G\* único fica abaixo da mediana de 52M/256 (43) | tabela compacta (MCA base → RF, ΔMCA, Δσ) com cores |
| 26 | **Degradação por corrupção — 18M** | Degradação média cai nas 3 configs (18,39 → 16,80; 18,05 → 16,22; 17,78 → 16,20) · 256KB: melhora nas 15 corrupções · exceções mínimas (Pixelate +0,08, Zoom Blur +0,06) | heatmap/tabela Δ (base − RF) por corrupção |
| 27 | **Degradação por corrupção — 52M** | Médias: 18,88 → 17,69; 20,88 → 17,17; 20,69 → 17,31 · maiores ganhos em ruído (Gaussian, Impulse, Shot) e Glass Blur: 4–11 pp · 52M/512: melhora nas 15 · onde σ subiu, a robustez média ainda melhorou | idem |
| 28 | **Weighted Fitness: quanto peso dar à robustez?** | w = 0 → menor σ do estudo (0,256) · w ≤ 0,4: MCA ~70,8% estável · w ≥ 0,6: MCA cai, σ sobe · w = 1 ainda supera o baseline → parte do ganho vem de G\* | gráfico MCA e σ × w (pgfplots) com linhas de referência baseline/RF |
| 29 | **O que os resultados dizem** | MCA atua como **regularizador** da busca · custo de avaliar corrupções concentrado uma vez no preditor · ressalva honesta: G\* também contribui para a estabilidade | `\goodbox` + `\alertbox` |

## 5. Conclusão (~2 min)

| # | Slide | Conteúdo | Visual |
|---|---|---|---|
| 30 | **Conclusões** | Hipótese confirmada · 4 números-resumo: **−39,15 pp** (fragilidade do baseline), **+2,94 pp** MCA, **−52%** σ, **MAE ≤ 0,25%** do preditor | 4 `\bignumber` em grade |
| 31 | **Limitações e trabalhos futuros** | Esq.: hiperparâmetros evolutivos fixos · um só par dataset/supernet · só corrupções naturais · Dir.: outros datasets/supernets · *aging evolution* · otimizar hiperparâmetros · robustez adversarial · WF em todas as configs · G\* por config | duas colunas |
| — | **Obrigado** | Contato, link do VWW-C, agradecimentos (FAPEAL/CNPq, se aplicável) | slide sem cabeçalho |

## Backup (depois do "Obrigado")

| # | Slide | Para qual pergunta |
|---|---|---|
| B1 | Hiperparâmetros da busca evolutiva + seleção (*top-K*, mutação, crossover) | "Por que esses valores?" |
| B2 | Tabela completa de degradação — 18M (valores absolutos) | "E a corrupção X?" |
| B3 | Tabela completa de degradação — 52M (valores absolutos) | idem |
| B4 | Validação do preditor: tabela completa (N, MAE, RMSE, bias) | "Por que 18M/128KB tem N = 240?" |
| B5 | Protocolo de avaliação: metade das imagens para avaliar, metade limpa para recalibrar o BN | "Como as subredes foram avaliadas?" |

---

## Notas

1. **Ritmo**: se o ensaio passar de 30 min, os candidatos a cortar são 23 (citar
   o resultado direto no 24), 12 (citar em uma linha no 13) e fundir 26+27.
2. **Figuras**: os PDFs dos Caps. 4 e 5 entram direto. As tabelas serão refeitas em
   LaTeX no estilo do template (mais legíveis que as da monografia com `\hline`).
