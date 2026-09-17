<?php
declare(strict_types=1);

namespace __NAMESPACE__;

use ComponentsCore\BaseComponent;
use FrontendCore\Assets\AssetsConstantes;

final class __CLASS__ extends BaseComponent
{
    public function __construct(string $label = '')
    {
        parent::__construct(get_defined_vars());
        $label = htmlspecialchars($label, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
        $this->html = <<<HTML
<div class="__CSS_CLASS__">{$label}<!--uni-slot--></div>
HTML;
        $this->stackCss(scope: AssetsConstantes::CSS_SCOPE_STATIC, css: <<<CSS
.__CSS_CLASS__ { display: block; }
CSS
        );
        $this->stackScript(scope: AssetsConstantes::SCRIPT_SCOPE_STATIC, js: <<<JS
// Comportamento compartilhado do componente.
JS
        );
    }
}
