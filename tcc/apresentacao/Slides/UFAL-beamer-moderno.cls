% ------------------------------------------------------------------------------- %
%  UFAL-beamer-moderno.cls                                                        %
%                                                                                 %
%  Releitura "moderna" do UFAL-beamer.cls (baseado em hsneto/ifes-beamer, MIT).   %
%  Mantém a estrutura original:                                                   %
%    - cabeçalho com navegação por seções (miniframes)                            %
%    - faixa de título em cada frame                                              %
%    - rodapé segmentado (autor | instituição | título | data + página)           %
%    - frame automático de abertura de seção                                      %
%  e moderniza: paleta derivada do logo do IC/UFAL, tipografia Fira Sans,         %
%  rodapé fino com barra de progresso, blocos arredondados, 16:9 por padrão.      %
%                                                                                 %
%  Opções da classe:                                                              %
%    43      -> proporção 4:3 (padrão é 16:9)                                     %
%    montserrat | fira | roboto | arial | helvet -> fonte (padrão: montserrat)  %
%    demais opções são repassadas ao beamer                                       %
% ------------------------------------------------------------------------------- %
%  MIT License — Copyright (c) 2018 Humberto da Silva Neto                        %
%  https://github.com/hsneto/ifes-beamer                                          %
% ------------------------------------------------------------------------------- %

\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{UFAL-beamer-moderno}[2026/11/09, v2.0]

\newif\if@ufal@wide   \@ufal@widetrue
\def\ufal@font{montserrat}
\DeclareOption{43}{\@ufal@widefalse}
\DeclareOption{fira}{\def\ufal@font{fira}}
\DeclareOption{roboto}{\def\ufal@font{roboto}}
\DeclareOption{montserrat}{\def\ufal@font{montserrat}}
\DeclareOption{arial}{\def\ufal@font{arial}}
\DeclareOption{helvet}{\def\ufal@font{helvet}}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{beamer}}
\ProcessOptions\relax

\PassOptionsToPackage{svgnames}{xcolor}
\if@ufal@wide
  \LoadClass[t,compress,10pt,aspectratio=169]{beamer}
\else
  \LoadClass[t,compress,9pt]{beamer}
\fi

% --------------------------------------------------- %
%                       Pacotes                       %
% --------------------------------------------------- %
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[brazil]{babel}
\usepackage{amsmath,amsfonts,amssymb,mathtools}
\usepackage{graphicx}
\usepackage{subcaption}
\usepackage{pdfpages}
\usepackage{etoolbox}
\usepackage{tikz}
\usetikzlibrary{positioning,calc}

% Fonte do texto (opção da classe): montserrat (padrão) | fira | roboto | arial | helvet
\def\ufal@tmp{fira}\ifx\ufal@font\ufal@tmp
  \usepackage[sfdefault,book]{FiraSans}
\fi
\def\ufal@tmp{roboto}\ifx\ufal@font\ufal@tmp
  \usepackage[sfdefault]{roboto}
\fi
\def\ufal@tmp{montserrat}\ifx\ufal@font\ufal@tmp
  % regular + semibold: bom contraste no projetor sem ficar pesado;
  % scale=0.9 compensa a largura da Montserrat (ocupa ~ o mesmo espaço da Fira).
  \usepackage[regular,semibold,scale=0.9]{montserrat}
  \renewcommand*\familydefault{\sfdefault}
\fi
\def\ufal@tmp{arial}\ifx\ufal@font\ufal@tmp
  % Arial não tem pacote para pdfLaTeX; TeX Gyre Heros é praticamente idêntica
  % (mesmo desenho Helvetica/Arial e mesmas métricas).
  \usepackage{tgheros}
\fi
\def\ufal@tmp{helvet}\ifx\ufal@font\ufal@tmp
  \usepackage[scaled=0.95]{helvet}
\fi
\renewcommand*\familydefault{\sfdefault}
\usefonttheme[onlymath]{serif}

\usepackage[font=footnotesize,labelfont={color=ufalBlue,bf}]{caption}
\newcommand{\fonte}[1]{%
  \vspace{0.25\baselineskip}%
  \caption*{\scriptsize\color{ufalSlate}Fonte: {#1}}}
\renewcommand*{\thefootnote}{(\arabic{footnote})}

% --------------------------------------------------- %
%                 Paleta (logo IC/UFAL)               %
% --------------------------------------------------- %
% Azuis e vermelho extraídos do logo do Instituto de Computação.
\definecolor{ufalNavy}{HTML}{1B2550}    % azul muito escuro  -> cabeçalho, títulos
\definecolor{ufalBlue}{HTML}{34479A}    % azul do logo       -> estrutura
\definecolor{ufalSky}{HTML}{5B7FD1}     % azul claro         -> marcadores secundários
\definecolor{ufalTint}{HTML}{EEF1F9}    % azul quase branco  -> fundo de blocos
\definecolor{ufalRed}{HTML}{D7261E}     % vermelho do logo   -> acento (uso pontual)
\definecolor{ufalAccent}{HTML}{5AA2F0}   % azul claro -> acento decorativo (filetes, marcadores)
\definecolor{ufalAccentText}{HTML}{2A72CF} % azul claro legível -> \emph e textos de destaque
\definecolor{ufalRedTint}{HTML}{FCECEB}
\definecolor{ufalGreen}{HTML}{1E7F5C}
\definecolor{ufalGreenTint}{HTML}{E8F5EF}
\definecolor{ufalInk}{HTML}{1F2430}     % texto (preto suavizado)
\definecolor{ufalSlate}{HTML}{5A6270}   % texto secundário / fontes
\definecolor{ufalLine}{HTML}{D9DEE8}    % divisórias

% Nomes antigos -> mantidos para compatibilidade com slides do template original
\colorlet{airforceblue}{ufalBlue}
\colorlet{aliceblue}{ufalTint}
\colorlet{cadmiumgreen}{ufalGreen}
\colorlet{honeydew}{ufalGreenTint}
\colorlet{carnelian}{ufalRed}
\colorlet{melon}{ufalRedTint}
\colorlet{ashgrey}{ufalSlate}
\definecolor{lightgray}{HTML}{F1F3F6}
\colorlet{cadetgrey}{ufalLine}
\colorlet{orange-red}{ufalRed}
\definecolor{peach-orange}{HTML}{FDE7D6}
\definecolor{pastelyellow}{HTML}{FFF4D6}
\definecolor{uscgold}{HTML}{E0A100}
\definecolor{purpleheart}{HTML}{6741A8}
\definecolor{lightmauve}{HTML}{EFEAFB}
\definecolor{darkbrown}{HTML}{7A4E22}
\definecolor{moccasin}{HTML}{FBF2E6}
\colorlet{mainColor1}{ufalBlue}
\colorlet{mainColor2}{ufalTint}

% --------------------------------------------------- %
%                 Estrutura / cores                   %
% --------------------------------------------------- %
\usecolortheme[named=ufalBlue]{structure}
\useinnertheme{circles}
\useoutertheme[subsection=false]{miniframes}
\setbeamertemplate{navigation symbols}{}

\setbeamercolor*{normal text}{fg=ufalInk,bg=white}
\setbeamercolor*{example text}{fg=ufalGreen}
\setbeamercolor*{alerted text}{fg=ufalRed}
\setbeamercolor{titlelike}{fg=ufalNavy}

\newcommand{\exemple}[1]{{\color{ufalGreen}#1}}
\renewcommand{\emph}[1]{\textcolor{ufalAccentText}{\textbf{#1}}}

% Fontes
\setbeamerfont{frametitle}{size=\Large,series=\bfseries}
\setbeamerfont{framesubtitle}{size=\small,series=\mdseries}
\setbeamerfont{title}{size=\LARGE,series=\bfseries}
\setbeamerfont{subtitle}{size=\normalsize,series=\mdseries}
\setbeamerfont{section in head/foot}{size=\tiny}
\setbeamerfont{footline}{size=\tiny}
\setbeamerfont{block title}{size=\normalsize,series=\bfseries}

% Itens: círculos sólidos no 1º nível, traço no 2º
\setbeamercolor{itemize item}{fg=ufalBlue}
\setbeamercolor{itemize subitem}{fg=ufalSky}
\setbeamercolor{enumerate item}{fg=ufalBlue}
\setbeamercolor{description item}{fg=ufalBlue}
\setbeamertemplate{itemize item}{\tikz[baseline=-0.6ex]\fill[ufalBlue](0,0)circle(0.55ex);}
\setbeamertemplate{itemize subitem}{\tikz[baseline=-0.6ex]\draw[ufalSky,line width=0.6pt](0,0)circle(0.45ex);}
\setbeamertemplate{enumerate item}{%
  \usebeamercolor[bg]{item projected}%
  \raisebox{-0.1ex}{\colorbox{ufalBlue}{\color{white}\scriptsize\bfseries\insertenumlabel}}}
\setlength{\leftmargini}{1.4em}

% --------------------------------------------------- %
%       Cabeçalho: navegação + filete vermelho        %
% --------------------------------------------------- %
\setbeamercolor{section in head/foot}{fg=white!60!ufalNavy,bg=ufalNavy}
\setbeamercolor{section in head/foot shaded}{fg=white!45!ufalNavy}
\setbeamercolor{mini frame}{fg=white}
\setbeamertemplate{mini frame}{%
  \begin{pgfpicture}{0pt}{0pt}{0.12cm}{0.1cm}
    \pgfpathcircle{\pgfpoint{0.05cm}{0.05cm}}{0.04cm}\pgfusepath{fill}
  \end{pgfpicture}}
\setbeamertemplate{mini frame in current subsection}{%
  \begin{pgfpicture}{0pt}{0pt}{0.12cm}{0.1cm}
    \pgfsetlinewidth{0.3pt}
    \pgfpathcircle{\pgfpoint{0.05cm}{0.05cm}}{0.04cm}\pgfusepath{stroke}
  \end{pgfpicture}}
\setbeamertemplate{mini frame in other subsection}{%
  \begin{pgfpicture}{0pt}{0pt}{0.12cm}{0.1cm}
    \pgfsetlinewidth{0.3pt}
    \pgfpathcircle{\pgfpoint{0.05cm}{0.05cm}}{0.04cm}\pgfusepath{stroke}
  \end{pgfpicture}}

\defbeamertemplate*{headline}{ufal modern}{%
  \begin{beamercolorbox}[ht=2.6ex,dp=2.4ex,wd=\paperwidth]{section in head/foot}%
    \insertnavigation{\paperwidth}%
  \end{beamercolorbox}%
  \nointerlineskip
  {\color{ufalAccent}\rule{\paperwidth}{0.6pt}}%
}

% --------------------------------------------------- %
%      Título do frame: texto escuro + marcador       %
% --------------------------------------------------- %
\setbeamertemplate{frametitle}{%
  \nointerlineskip
  \vskip1.2ex
  \begin{minipage}[c]{\textwidth}
    \begin{tikzpicture}[baseline=(t.base)]
      \node[inner sep=0pt, anchor=west, text width=\dimexpr\textwidth-1em\relax,
            font=\usebeamerfont{frametitle}, text=ufalNavy] (t) at (0.9em,0)
            {\strut\insertframetitle};
      \fill[ufalAccent] ($(t.north west)+(-0.9em,-0.25ex)$) rectangle
                     ($(t.south west)+(-0.55em,0.25ex)$);
    \end{tikzpicture}%
    \ifx\insertframesubtitle\@empty\else
      \par\vskip0.2ex\hskip0.9em%
      {\usebeamerfont{framesubtitle}\color{ufalSlate}\insertframesubtitle}%
    \fi
  \end{minipage}%
  \par\vskip0.6ex
  {\color{ufalLine}\rule{\textwidth}{0.4pt}}%
  \vskip0.2ex
}

% --------------------------------------------------- %
%     Rodapé: fino, claro, com barra de progresso      %
% --------------------------------------------------- %
\setbeamercolor{footline}{fg=ufalSlate,bg=white}
\setbeamercolor{page in head/foot}{fg=white,bg=ufalBlue}

\defbeamertemplate*{footline}{ufal modern}{%
  \leavevmode
  % barra de progresso (vermelho sobre linha clara)
  \begin{tikzpicture}[inner sep=0pt, outer sep=0pt]
    \fill[ufalLine] (0,0) rectangle (\paperwidth,0.5pt);
    \ifnum\inserttotalframenumber>1
      \pgfmathsetmacro{\ufal@prog}{min(1,\insertframenumber/\inserttotalframenumber)}
      \fill[ufalBlue] (0,0) rectangle (\ufal@prog\paperwidth,0.5pt);
    \fi
  \end{tikzpicture}%
  \par\nointerlineskip
  \hbox{%
    \begin{beamercolorbox}[wd=.30\paperwidth,ht=2.6ex,dp=1.2ex,left,leftskip=1.2em]{footline}%
      \usebeamerfont{footline}\textbf{\insertshortauthor}%
      \ifx\insertshortinstitute\@empty\else~\textperiodcentered~\insertshortinstitute\fi
    \end{beamercolorbox}%
    \begin{beamercolorbox}[wd=.50\paperwidth,ht=2.6ex,dp=1.2ex,center]{footline}%
      \usebeamerfont{footline}\insertshorttitle
    \end{beamercolorbox}%
    \begin{beamercolorbox}[wd=.20\paperwidth,ht=2.6ex,dp=1.2ex,right,rightskip=1.2em]{footline}%
      \usebeamerfont{footline}\insertshortdate\hspace{0.8em}%
      {\color{ufalBlue}\bfseries\insertframenumber}\,/\,\inserttotalframenumber
    \end{beamercolorbox}%
  }%
  \vskip0pt
}

% --------------------------------------------------- %
%                  Página de título                   %
% --------------------------------------------------- %
\newcommand\supervisor[1]{\def\insertsupervisor{#1}}
\newcommand\cosupervisor[1]{\def\insertcosupervisor{#1}}
\newcommand\supervisortitle{Orientador}
\newcommand\cosupervisortitle{Coorientador}
\newcommand\doctype[1]{\def\insertdoctype{#1}}
\newcommand\logo@path{}
\newcommand\ufallogo[1]{\renewcommand\logo@path{#1}}
\let\insertsupervisor\relax
\let\insertcosupervisor\relax
\def\insertdoctype{}

\defbeamertemplate*{title page}{ufal modern}{%
  \vskip0pt plus 1fill
  \hskip1.2cm
  \begin{minipage}{0.70\paperwidth}
    \raggedright
    {\usebeamerfont{subtitle}\color{ufalAccentText}\scshape\insertdoctype\par}
    \vskip1.2ex
    {\usebeamerfont{title}\color{ufalNavy}\inserttitle\par}
    \ifx\insertsubtitle\@empty\else
      \vskip1ex
      {\usebeamerfont{subtitle}\color{ufalSlate}\insertsubtitle\par}
    \fi
    \vskip2.2ex
    {\color{ufalBlue}\rule{3cm}{1.2pt}}
    \vskip2.2ex
    {\large\bfseries\color{ufalInk}\insertauthor\par}
    \vskip0.6ex
    {\small\color{ufalSlate}%
      \ifx\insertsupervisor\relax\else
        \supervisortitle:~\insertsupervisor\par
      \fi
      \ifx\insertcosupervisor\relax\else
        \cosupervisortitle:~\insertcosupervisor\par
      \fi}
    \vskip2.4ex
    {\footnotesize\color{ufalSlate}\insertinstitute\par
     \vskip0.4ex\insertdate\par}
  \end{minipage}
  \vskip0pt plus 1.4fill
}

% Fundos desenhados em coordenadas absolutas (funcionam já na 1a compilação).
\newcommand{\ufal@titlebg}{%
  \begin{tikzpicture}[inner sep=0pt]
    \useasboundingbox (0,0) rectangle (\paperwidth,\paperheight);
    \fill[ufalNavy] (0,0) rectangle (0.9cm,\paperheight);
    \fill[ufalAccent] (0.9cm,0) rectangle (0.98cm,\paperheight);
    \ifx\logo@path\@empty\else
      \node[anchor=north east] at ($(\paperwidth,\paperheight)+(-0.8cm,-0.7cm)$)
        {\includegraphics[height=1.3cm]{\logo@path}};
    \fi
  \end{tikzpicture}}
\newcommand{\ufal@sectionbg}{%
  \begin{tikzpicture}[inner sep=0pt]
    \useasboundingbox (0,0) rectangle (\paperwidth,\paperheight);
    \fill[ufalNavy] (0,0) rectangle (0.48\paperwidth,\paperheight);
    \fill[ufalAccent] (0.48\paperwidth,0) rectangle ($(0.48\paperwidth,\paperheight)+(0.8mm,0)$);
    \node[anchor=south west, text=white!25!ufalNavy,
          font=\fontsize{70}{70}\selectfont\bfseries]
      at ($(0,0.5\paperheight)+(0.9cm,1.0cm)$) {\two@digits{\insertsectionnumber}};
    \node[anchor=north west, text=white, align=left, font=\LARGE\bfseries]
      at ($(0,0.5\paperheight)+(0.9cm,0.6cm)$)
      {\parbox{0.38\paperwidth}{\raggedright\hyphenpenalty=10000\exhyphenpenalty=10000\insertsectionhead\par}};
  \end{tikzpicture}}

% Página de título sem cabeçalho/rodapé
\let\ufal@titlepage\titlepage
\renewcommand{\titlepage}{%
  {\setbeamertemplate{headline}{}\setbeamertemplate{footline}{}%
   \setbeamertemplate{background}{\ufal@titlebg}%
   \begin{frame}[plain,noframenumbering]\ufal@titlepage\end{frame}}}
\newcommand{\maketitleframe}{\titlepage}

% --------------------------------------------------- %
%                 Frames de seção                     %
% --------------------------------------------------- %
% Número grande + título à esquerda; roteiro à direita destacando a seção atual.
\setbeamercolor{section in toc}{fg=ufalInk}
\setbeamertemplate{section in toc shaded}[default][55]
\setbeamertemplate{section in toc}{%
  {\color{ufalBlue}\bfseries\two@digits{\inserttocsectionnumber}}\hspace{0.8em}%
  \inserttocsection\par\vskip0.4ex}

\AtBeginSection[]{%
  {\setbeamertemplate{headline}{}%
   \setbeamertemplate{background}{\ufal@sectionbg}%
   \begin{frame}[c,plain,noframenumbering]
    \hfill
    \begin{minipage}{0.40\paperwidth}
      \small\tableofcontents[currentsection,hideallsubsections]
    \end{minipage}
   \end{frame}}%
}

% --------------------------------------------------- %
%                      Blocos                         %
% --------------------------------------------------- %
\setbeamertemplate{blocks}[rounded][shadow=false]
\setbeamercolor*{block title}{fg=white,bg=ufalBlue}
\setbeamercolor*{block body}{fg=ufalInk,bg=ufalTint}
\setbeamercolor*{block title alerted}{fg=white,bg=ufalRed}
\setbeamercolor*{block body alerted}{fg=ufalInk,bg=ufalRedTint}
\setbeamercolor*{block title example}{fg=white,bg=ufalGreen}
\setbeamercolor*{block body example}{fg=ufalInk,bg=ufalGreenTint}

% Notas de destaque sem título (barra lateral colorida)
\newcommand{\ufal@note}[3]{%
  \par\smallskip\noindent
  \begin{tikzpicture}
    \node[fill=#2, inner xsep=8pt, inner ysep=6pt, text width=\dimexpr\linewidth-16pt-3pt\relax,
          align=left, rounded corners=2pt] (n) {#3};
    \fill[#1] (n.north west) rectangle ([xshift=3pt]n.south west);
  \end{tikzpicture}\par\smallskip}
\newcommand{\notebox}[1]{\ufal@note{ufalBlue}{ufalTint}{#1}}
\newcommand{\alertbox}[1]{\ufal@note{ufalRed}{ufalRedTint}{#1}}
\newcommand{\goodbox}[1]{\ufal@note{ufalGreen}{ufalGreenTint}{#1}}

% Destaque numérico (ex.: "+12,3 p.p.")
\newcommand{\bignumber}[2]{%
  \begin{minipage}[t]{\linewidth}\centering
    {\fontsize{26}{28}\selectfont\bfseries\color{ufalBlue}#1\par}
    \vskip2pt
    {\footnotesize\color{ufalSlate}#2\par}
  \end{minipage}}

% Fonte de figura compacta
\newcommand{\src}[1]{{\scriptsize\color{ufalSlate}Fonte: #1}}

% Caixas coloridas do template original (compatibilidade)
\newcommand{\ufal@oldbox}[3]{\begin{center}\ufal@note{#1}{#2}{#3}\end{center}}
\newcommand{\boxblue}[1]{\ufal@oldbox{ufalBlue}{ufalTint}{#1}}
\newcommand{\boxgreen}[1]{\ufal@oldbox{ufalGreen}{ufalGreenTint}{#1}}
\newcommand{\boxyellow}[1]{\ufal@oldbox{uscgold}{pastelyellow}{#1}}
\newcommand{\boxpurple}[1]{\ufal@oldbox{purpleheart}{lightmauve}{#1}}
\newcommand{\boxorange}[1]{\ufal@oldbox{ufalRed}{peach-orange}{#1}}
\newcommand{\boxbrown}[1]{\ufal@oldbox{darkbrown}{moccasin}{#1}}
\newcommand{\boxgrey}[1]{\ufal@oldbox{ufalSlate}{lightgray}{#1}}
\newcommand{\boxblack}[1]{\ufal@oldbox{ufalInk}{white}{#1}}

% Tabelas
\setbeamercolor{caption name}{fg=ufalBlue}

\mode<all>
