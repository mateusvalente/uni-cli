<?php
declare(strict_types=1);

$root = getcwd();
require $root . '/vendor/autoload.php';
$build = require $root . '/storage/framework/manifest/build.php';
new FrontendCore\View\CompiledTemplate($root, $build['templates'], $build['components']);
try {
    $tag = $argv[1] ?? '';
    $entry = $build['components'][$tag] ?? throw new RuntimeException('Componente desconhecido: ' . $tag);
    $props = json_decode($argv[2] ?? '{}', true, flags: JSON_THROW_ON_ERROR);
    $directory = $argv[3] ?? ($root . '/storage/storybook/' . $tag);
    $component = new ($entry['class'])(...$props);
    $files = (new ComponentsCore\StorybookExporter())->export($component, $directory);
    echo json_encode($files, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . PHP_EOL;
} catch (Throwable $error) {
    fwrite(STDERR, $error->getMessage() . PHP_EOL);
    exit(1);
}
