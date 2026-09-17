<?php

declare(strict_types=1);

use ApplicationCore\Routing\Attributes\Route;

/** Leitura de metadados executada exclusivamente pelo uni.py build. */
function compileRoutes(string $root): array
{
    $manifest = json_decode(file_get_contents($root . '/composer.json'), true, flags: JSON_THROW_ON_ERROR);
    $vendor = $manifest['config']['vendor-dir'] ?? 'vendor';
    if (!is_string($vendor) || $vendor === '') {
        throw new RuntimeException('config.vendor-dir deve ser um caminho nao vazio.');
    }
    $vendorPath = str_starts_with($vendor, '/') || preg_match('/^[A-Za-z]:[\\\\\/]/', $vendor)
        ? $vendor : $root . '/' . $vendor;
    require $vendorPath . '/autoload.php';
    if (!class_exists(\ApplicationCore\Modules\Modules::class)) {
        throw new RuntimeException('ApplicationCore nao instalado. Instale uniube/application-core com Composer antes de executar uni build.');
    }
    $classMap = require $vendorPath . '/composer/autoload_classmap.php';

    $source = realpath($root . '/src');
    $files = [];
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($source, FilesystemIterator::SKIP_DOTS));
    foreach ($iterator as $file) {
        if ($file->isFile() && str_ends_with($file->getFilename(), 'Controller.php')) {
            $files[$file->getRealPath()] = false;
        }
    }

    $routes = [];
    $registered = [];
    $byPath = [];
    ksort($classMap);
    foreach ($classMap as $class => $file) {
        $file = realpath($file);
        if (!array_key_exists($file, $files)) {
            continue;
        }
        $files[$file] = true;
        if (!class_exists($class)) {
            continue;
        }
        $controller = new ReflectionClass($class);
        if ($controller->isAbstract()) {
            continue;
        }
        foreach ($controller->getMethods() as $method) {
            $attributes = $method->getAttributes(Route::class);
            if ($attributes === []) {
                continue;
            }
            $context = $class . '::' . $method->getName();
            if (!$method->isPublic() || $method->isConstructor() || $method->isDestructor()) {
                throw new RuntimeException("{$context}: a rota deve estar em um metodo publico de acao.");
            }
            if (count($attributes) !== 1) {
                throw new RuntimeException("{$context}: declare apenas um atributo Route por metodo.");
            }
            // getArguments le atributos sem instanciar controllers ou executar acoes.
            $arguments = $attributes[0]->getArguments();
            $route = (static function (
                string $path,
                array $methods,
                bool $cache,
                int $cacheTtl = 60,
                bool $csrf = true,
                string $contentType = ApplicationCore\Http\Response::JSON,
                ?string $cacheGroup = null,
            ): array {
                if ($cacheTtl < 1 || ($cache && array_diff($methods, ['GET', 'HEAD']) !== [])) {
                    throw new RuntimeException('Use cacheTtl positivo e cache apenas em GET/HEAD.');
                }
                ApplicationCore\Http\Response::formatOf($contentType);
                return compact('path', 'methods', 'cache', 'cacheTtl', 'csrf', 'contentType', 'cacheGroup');
            })(...$arguments);
            $path = $route['path'];
            if (!str_starts_with($path, '/') || preg_match('/[\s?#]/', $path) || str_starts_with($path, '/assets/')) {
                throw new RuntimeException("{$context}: caminho de rota invalido: {$path}");
            }
            preg_match_all('/\{([A-Za-z_][A-Za-z0-9_]*)\}/', $path, $matches);
            $variables = $matches[1];
            $literal = preg_replace('/\{[A-Za-z_][A-Za-z0-9_]*\}/', '', $path);
            $shape = preg_replace('/\{[A-Za-z_][A-Za-z0-9_]*\}/', '{}', $path);
            if (strpbrk($literal, '{}') !== false || count(array_unique($variables)) !== count($variables)) {
                throw new RuntimeException("{$context}: variaveis da rota invalidas ou repetidas.");
            }
            if ($route['methods'] === [] || !array_is_list($route['methods'])) {
                throw new RuntimeException("{$context}: informe uma lista nao vazia de verbos HTTP.");
            }
            foreach ($route['methods'] as $verb) {
                if (!is_string($verb) || !preg_match('/^[!#$%&\'*+.^_`|~0-9A-Za-z-]+$/D', $verb)) {
                    throw new RuntimeException("{$context}: verbo HTTP invalido.");
                }
                $key = $verb . ' ' . $shape;
                if (isset($registered[$key])) {
                    throw new RuntimeException("Rota duplicada: {$verb} {$path} em {$context} e {$registered[$key]}.");
                }
                $registered[$key] = $context;
            }
            if (isset($byPath[$path]) && $byPath[$path] !== $class) {
                throw new RuntimeException("Caminho {$path} associado a controllers diferentes: {$byPath[$path]} e {$class}.");
            }
            $byPath[$path] = $class;
            $start = $method->getStartLine() - 1;
            $length = $method->getEndLine() - $start;
            $lines = file($file);
            $methodCode = implode('', array_slice($lines, $start, $length));
            $routeMeta[$path] = [
                'path' => $path,
                'controller' => $class,
                'file' => $file,
                'method' => $method->getName(),
                'method_code' => $methodCode,
            ];
            // Valida Query/Body/Slug no controller; nao grava em routes.json.
            (new ApplicationCore\Routing\ParameterInspector())->describe($method, $variables, $route['cache']);
        }
    }
    foreach ($files as $file => $mapped) {
        if (!$mapped) {
            throw new RuntimeException("Controller fora do autoload Composer: {$file}");
        }
    }
    foreach ($byPath as $path => $controller) {
        $routes[] = $routeMeta[$path] ?? ['path' => $path, 'controller' => $controller];
    }
    usort($routes, static fn (array $a, array $b): int => [$a['path'], $a['controller']] <=> [$b['path'], $b['controller']]);
    return ['routes' => $routes];
}

function calculateRouteRevision(array $route, array $frontend, string $root): string
{
    $hashes = [];
    $file = $route['file'] ?? null;
    $methodCode = $route['method_code'] ?? '';
    if (is_string($file) && is_file($file)) {
        $hashes[] = hash_file('sha256', $file);
    }
    if ($methodCode !== '') {
        $hashes[] = hash('sha256', $methodCode);
    }
    $searchCode = $methodCode !== '' ? $methodCode : (is_string($file) && is_file($file) ? file_get_contents($file) : '');
    preg_match_all('/([A-Za-z0-9_\-\/]+\.html\.php)/', $searchCode, $matches);
    foreach (array_unique($matches[1] ?? []) as $match) {
        $pageFile = (is_string($file) ? realpath(dirname($file) . '/' . $match) : false)
            ?: realpath($root . '/' . $match)
            ?: realpath($root . '/src/' . $match);
        if ($pageFile !== false && is_file($pageFile)) {
            $hashes[] = hash_file('sha256', $pageFile);
            $base = preg_replace('/\.html\.php$/', '', $pageFile);
            if (is_file($base . '.css')) {
                $hashes[] = hash_file('sha256', $base . '.css');
            }
            if (is_file($base . '.js')) {
                $hashes[] = hash_file('sha256', $base . '.js');
            }
            $normPage = str_replace('\\', '/', $pageFile);
            $normRoot = str_replace('\\', '/', $root) . '/';
            $relPage = str_starts_with($normPage, $normRoot) ? substr($normPage, strlen($normRoot)) : '';
            if ($relPage !== '' && isset($frontend['dependencies'][$relPage])) {
                foreach ($frontend['dependencies'][$relPage] as $componentTag) {
                    if (isset($frontend['components'][$componentTag]['file']) && is_file($frontend['components'][$componentTag]['file'])) {
                        $hashes[] = hash_file('sha256', $frontend['components'][$componentTag]['file']);
                    }
                }
            }
        }
    }
    if ($hashes === []) {
        $hashes[] = hash('sha256', $route['path'] . ':' . ($route['controller'] ?? ''));
    }
    return substr(hash('sha256', implode("\0", $hashes)), 0, 8);
}

function runtimeRoutes(array $routes, array $frontend = [], string $root = ''): array
{
    $table = ['static' => [], 'dynamic' => []];
    foreach ($routes as $route) {
        $path = $route['path'];
        $pathParameters = ApplicationCore\Routing\ActionResolver::pathParameters($path);
        $revision = calculateRouteRevision($route, $frontend, $root);
        $entry = [
            'path' => $path,
            'controller' => $route['controller'],
            'revision' => $revision,
            'path_parameters' => $pathParameters,
        ];
        if ($pathParameters === []) {
            $table['static'][$path] = $entry;
            continue;
        }
        $parts = preg_split('/(\{[A-Za-z_][A-Za-z0-9_]*\})/', $path, flags: PREG_SPLIT_DELIM_CAPTURE);
        $pattern = '';
        $specificity = 0;
        foreach ($parts as $part) {
            if (preg_match('/^\{([A-Za-z_][A-Za-z0-9_]*)\}$/D', $part, $match)) {
                $pattern .= '(?P<' . $match[1] . '>[^/]+)';
            } else {
                $pattern .= preg_quote($part, '~');
                $specificity += strlen($part);
            }
        }
        $entry['pattern'] = '~^' . $pattern . '$~D';
        if (@preg_match($entry['pattern'], '') === false) {
            throw new RuntimeException("{$route['controller']} {$path}: nao foi possivel compilar o padrao da rota.");
        }
        $entry['specificity'] = $specificity;
        $table['dynamic'][] = $entry;
    }
    usort($table['dynamic'], static fn (array $a, array $b): int => $b['specificity'] <=> $a['specificity']);
    return $table;
}

ob_start();
try {
    $root = realpath($argv[1] ?? '');
    if ($root === false || !is_dir($root . '/src')) {
        throw new RuntimeException('Raiz do projeto invalida ou pasta src ausente.');
    }
    $result = compileRoutes($root);
    if (isset($argv[2])) {
        $stage = $argv[2];
        $frontend = [];
        file_put_contents($stage . '/artifacts.json', '{}');
        foreach (\ApplicationCore\Modules\Modules::create() as $module) {
            $frontend = array_replace($frontend, $module->build($root, $stage));
        }
        $build = [
            'revision' => bin2hex(random_bytes(16)),
            'routing' => runtimeRoutes($result['routes'], $frontend, $root),
            ...$frontend,
        ];
        file_put_contents($stage . '/components.php', "<?php\nreturn " . var_export($build['components'] ?? [], true) . ";\n");
        if (file_put_contents($stage . '/assets.php', "<?php\nreturn " . var_export($build['assetsManifest'] ?? [], true) . ";\n") === false) {
            throw new RuntimeException('Falha ao gravar manifesto de assets.');
        }
        if (file_put_contents($stage . '/build.php', "<?php\n\ndeclare(strict_types=1);\n\nreturn " . var_export($build, true) . ";\n") === false) {
            throw new RuntimeException('Falha ao gravar o build PHP.');
        }
    }
    $unexpected = ob_get_clean();
    if ($unexpected !== '') {
        fwrite(STDERR, "UNEXPECTED OUTPUT:\n" . $unexpected . "\n");
        throw new RuntimeException('O carregamento dos controllers produziu saida inesperada.');
    }
    echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
} catch (Throwable $error) {
    if (ob_get_level() > 0) {
        ob_end_clean();
    }
    fwrite(STDERR, $error->getMessage() . PHP_EOL);
    exit(1);
}
