<!doctype html>
<html lang="pt-BR">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title><?= htmlspecialchars((string) ($title), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></title>
        <?php foreach ($metadata as $name => $value): ?>
            <meta name="<?= htmlspecialchars((string) ($name), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>" content="<?= htmlspecialchars((string) ($value), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>">
        <?php endforeach; ?>
        <?= $styles ?>
        <?= $scripts ?>
    </head>
    <body class="__CSS_CLASS__">
        <?= $header ?>
        <main id="conteudo">
            <?= $content ?>
        </main>
        <?= $footer ?>
    </body>
</html>
