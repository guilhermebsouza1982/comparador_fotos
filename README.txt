COMPARADOR DE FOTOS v5
=======================

PRINCIPAIS AJUSTES
------------------
- Corrige o erro do Tkinter "invalid command name ...update" ao fechar a janela de processamento.
- A analise pode ser interrompida pelo botao "Interromper e salvar".
- Ha a opcao "Interromper e ver parciais".
- Hashes, pHash e embeddings CLIP sao persistidos em disco durante a analise.
- As comparacoes CLIP (KNN) tambem sao persistidas.
- Em uma segunda execucao, arquivos que nao mudaram nao sao recalculados.
- Se todos os embeddings estiverem em cache, o modelo CLIP nem precisa ser carregado para essa etapa.
- Se a analise for interrompida, a proxima execucao continua usando o que ja foi salvo.
- O slider de 80% a 99% continua funcionando sem recalcular CLIP.
- Duplicatas exatas (SHA-256), pHash e CLIP sao mostrados na mesma tela.
- Botao para enviar fotos para a Lixeira do Windows.
- Caminho completo dos arquivos.
- HEIC/HEIF quando suportado pelo pillow-heif.

ARQUIVOS DE CACHE
-----------------
Na pasta analisada sao criados:
  .comparador_fotos_cache_v5.json
  .comparador_fotos_embeddings_v5.npz
  .comparador_fotos_edges_v5.npz
  .comparador_fotos_phash_v5.json

Nao apague esses arquivos se quiser continuar ou acelerar execucoes futuras.
Se uma foto for alterada, o programa detecta a mudanca pelo caminho, tamanho e data/hora de modificacao e recalcula apenas o necessario.

EXECUCAO
--------
comparar_fotos.bat "D:\Fotos"

Ou:
python comparador_fotos.py "D:\Fotos"

PARAMETROS OPCIONAIS
--------------------
--threshold 0.90
--batch 16
--workers 8
--knn 50

Exemplo:
python comparador_fotos.py "D:\Fotos" --threshold 0.90 --knn 50

GPU
---
O programa usa a GPU NVIDIA se o PyTorch CUDA estiver instalado e reconhecido.
Para a GTX 1650, a versao preparada usa PyTorch CUDA 12.6.

AVISO DO HUGGING FACE
---------------------
A mensagem:
  "You are sending unauthenticated requests to the HF Hub..."
e um aviso de limite/velocidade do download do modelo. Nao e um erro do programa.
Depois que o modelo for baixado, ele fica no cache local do Hugging Face e normalmente nao precisa ser baixado novamente.

INTERRUPCAO E PERSISTENCIA
--------------------------
Os dados sao salvos durante o processamento. Se voce clicar em "Interromper e salvar", o programa termina o pequeno lote em andamento, grava o estado e abre os resultados disponiveis.

Na proxima execucao:
- SHA/pHash de arquivos inalterados sao reutilizados;
- embeddings CLIP de arquivos inalterados sao reutilizados;
- comparacoes KNN ja calculadas sao reutilizadas quando o conjunto de arquivos, chaves e K forem iguais;
- pHash ja calculado e valido tambem e reutilizado.

IMPORTANTE
----------
O programa NAO apaga fotos definitivamente. O botao usa a Lixeira do Windows.
Sempre confira os grupos antes de excluir.


CACHE GLOBAL
============
Os arquivos de índice/cache agora ficam na subpasta "cache" da pasta do programa,
e NÃO na pasta que está sendo analisada.

Isso permite processar várias pastas diferentes e reaproveitar os mesmos hashes,
pHash, embeddings CLIP e relações KNN sempre que o arquivo já tiver sido indexado.

Exemplo:
C:\Python\comparador_fotos\cache\

O cache é identificado pelo caminho completo + tamanho + data de modificação do
arquivo. Se um arquivo for alterado, ele será processado novamente.
