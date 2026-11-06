using System.Collections.ObjectModel;
using System.ComponentModel;

namespace GmodAddonCompressor.Models;

public sealed record FullCompressionFileRow(string Path, string Type, long Bytes);

/// <summary>Presentation only. Completion is offline, never a GMod approval.</summary>
public sealed class FullCompressionPresentation : INotifyPropertyChanged
{
    private string _state = "empty";
    private bool _available = true;
    public ObservableCollection<FullCompressionFileRow> Files { get; } = new();
    public string Title { get; private set; } = "Compressão completa";
    public string Hint { get; private set; } = "Comece escolhendo o addon original.";
    public string NextStep { get; private set; } = "Próximo passo: escolher o addon";
    public string Phase { get; private set; } = "Aguardando configuração";
    public string Economy { get; private set; } = "Economia: disponível após concluir";
    public bool IsEmpty => _state == "empty";
    public bool IsRunning => _state == "running";
    public bool IsResult => _state == "result";
    public bool CanEdit => _available && !IsRunning;
    public bool ShowFiles => !IsEmpty;
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed() => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(string.Empty));
    public void SetAvailable(bool available) { _available = available; Changed(); }
    public void Prepare(bool hasInput)
    {
        _state = hasInput ? "setup" : "empty";
        Title = "Compressão completa";
        Hint = hasInput ? "Confira as pastas e as ferramentas antes de começar." : "Comece escolhendo o addon original.";
        NextStep = hasInput ? "Próximo passo: conferir pastas e ferramentas" : "Próximo passo: escolher o addon";
        Phase = "Aguardando configuração"; Economy = "Economia: disponível após concluir"; Changed();
    }
    public void Start()
    {
        _state = "running"; Title = "Processando a cópia"; Hint = "O original permanece intacto durante o processamento.";
        NextStep = "Processamento em andamento"; Phase = "Preparando cópia"; Changed();
    }
    public void UpdatePhase(string phase) { Phase = phase; Changed(); }
    public void Complete(string economy)
    {
        _state = "result"; Title = "Candidato preparado"; Hint = "Validação visual e funcional no GMod pendente.";
        NextStep = "Próximo passo: testar no jogo"; Phase = "Concluído offline · OFFLINE_PENDING_GMOD";
        Economy = economy; Changed();
    }
    public void Cancel()
    {
        _state = "cancelled"; Title = "Processamento cancelado"; Hint = "Não use a saída parcial. Escolha uma pasta final nova para repetir.";
        NextStep = "Próximo passo: revisar a configuração"; Phase = "Cancelado"; Changed();
    }
    public void Fail(string error)
    {
        _state = "failed"; Title = "Não foi possível concluir"; Hint = error;
        NextStep = "Próximo passo: revisar a falha e os logs"; Phase = "Falha"; Changed();
    }
}
