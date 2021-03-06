from binaryninja import (AnalysisCompletionEvent, Architecture, BasicBlock,
                         BinaryDataNotification, BinaryView, Function,
                         ILBranchDependence, MediumLevelILFunction,
                         MediumLevelILInstruction, MediumLevelILOperation,
                         RegisterValueType, Variable, VariableSourceType, BackgroundTaskThread,
                         log_info, worker_enqueue)
from functools import partial


class UnlockCompletionEvent(AnalysisCompletionEvent):
    def __init__(self, function):
        self.function = function
        super(UnlockCompletionEvent, self).__init__(function.view, UnlockCompletionEvent.check_next)

    def check_next(self):
        log_info("in completion event")
        checker = partial(check_next, self.view, self.function)
        worker_enqueue(checker)

def ace(evt):
    log_info("in ace")
    checker = partial(check_next, evt.view)
    worker_enqueue(checker)

def check_next(view, function):
    log_info("in check_next")
    target_queue = function.session_data.get('next', list())
    log_info(str(target_queue))
    next_target = None
    while target_queue:
        next_target = target_queue.pop()
        if next_target is None:
            continue
    if next_target is None:
        return
    log_info(f'{next_target:x} is next target')
    view.navigate(view.file.view, next_target)
    UnlockTaskThread(view.functions[0], next_target).start()

class UnlockTaskThread(BackgroundTaskThread):
    def __init__(self, function, addr):
        super(UnlockTaskThread, self).__init__()
        self.addr = addr
        self.function = function
        self.view = function.view

    def run(self):
        function = self.function
        il = function.get_low_level_il_at(self.addr).mapped_medium_level_il

        mmlil = il.function

        func = None
        while func is None:
            if il.operation in (MediumLevelILOperation.MLIL_RET,
                                MediumLevelILOperation.MLIL_RET_HINT):
                func = unret
            elif il.operation == MediumLevelILOperation.MLIL_IF:
                func = unjmp
            else:
                try:
                    il = mmlil[il.instr_index+1]
                except:
                    return

        UnlockCompletionEvent(function)
        run(func, il)

class BNILVisitor(object):
    def __init__(self, **kw):
        super(BNILVisitor, self).__init__()

    def visit(self, expression):
        method_name = 'visit_{}'.format(expression.operation.name)
        if hasattr(self, method_name):
            value = getattr(self, method_name)(expression)
        else:
            print(expression.operation)
            value = None
        return value

class ConditionVisitor(BNILVisitor):
    def visit_MLIL_CMP_E(self, expr):
        left = self.visit(expr.left)
        right = self.visit(expr.right)

        return left, right

    visit_MLIL_CMP_NE = visit_MLIL_CMP_E
    visit_MLIL_CMP_UGT = visit_MLIL_CMP_E
    visit_MLIL_CMP_ULE = visit_MLIL_CMP_E
    visit_MLIL_CMP_UGE = visit_MLIL_CMP_E
    visit_MLIL_CMP_ULT = visit_MLIL_CMP_E
    visit_MLIL_CMP_SGT = visit_MLIL_CMP_E
    visit_MLIL_CMP_SLE = visit_MLIL_CMP_E
    visit_MLIL_CMP_SGE = visit_MLIL_CMP_E
    visit_MLIL_CMP_SLT = visit_MLIL_CMP_E

    def visit_MLIL_VAR(self, expr):
        return expr.src

    def visit_MLIL_NOT(self, expr):
        return self.visit(expr.src)

    def visit_MLIL_CONST(self, expr):
        return expr.constant

    visit_MLIL_CONST_PTR = visit_MLIL_CONST

bb_cache = {}

cmp_pairs = {
    MediumLevelILOperation.MLIL_CMP_E: MediumLevelILOperation.MLIL_CMP_NE,
    MediumLevelILOperation.MLIL_CMP_NE: MediumLevelILOperation.MLIL_CMP_E,
    MediumLevelILOperation.MLIL_CMP_UGT: MediumLevelILOperation.MLIL_CMP_ULE,
    MediumLevelILOperation.MLIL_CMP_ULE: MediumLevelILOperation.MLIL_CMP_UGT,
    MediumLevelILOperation.MLIL_CMP_UGE: MediumLevelILOperation.MLIL_CMP_ULT,
    MediumLevelILOperation.MLIL_CMP_ULT: MediumLevelILOperation.MLIL_CMP_UGE,
    MediumLevelILOperation.MLIL_CMP_SGE: MediumLevelILOperation.MLIL_CMP_SLT,
    MediumLevelILOperation.MLIL_CMP_SLT: MediumLevelILOperation.MLIL_CMP_SGE,
    MediumLevelILOperation.MLIL_CMP_SGT: MediumLevelILOperation.MLIL_CMP_SLE,
    MediumLevelILOperation.MLIL_CMP_SLE: MediumLevelILOperation.MLIL_CMP_SGT,
    MediumLevelILOperation.MLIL_NOT: MediumLevelILOperation.MLIL_VAR,
    MediumLevelILOperation.MLIL_VAR: MediumLevelILOperation.MLIL_NOT
}

def unret(il : MediumLevelILInstruction):
    global bb_cache
    bb_cache = {}

    function = il.function.source_function
    # Step 1: find the return
    ret_addr = il.address
    log_info(repr(il))
    # Step 2: calculate the address to jump to
    current_esp = function.get_reg_value_at(ret_addr, 'esp')
    log_info(repr(current_esp))
    current_esp = current_esp.offset
    next_jump_value = function.get_stack_contents_at(
        ret_addr,
        current_esp,
        4
    )
    if next_jump_value.type == RegisterValueType.ConstantValue:
        next_jump_addr = next_jump_value.value
    else:
        return

    # Step 3: Identify the start
    print("Step 3")
    ret_il_ssa = il.ssa_form
    mmlil = il.function
    jump_variable_ssa = ret_il_ssa.dest.src
    jump_il = mmlil[mmlil.get_ssa_var_definition(jump_variable_ssa)]
    while jump_il.src.operation != MediumLevelILOperation.MLIL_CONST:
        new_var_ssa = jump_il.src.left.ssa_form.src
        jump_il = mmlil[mmlil.get_ssa_var_definition(new_var_ssa)]
    
    # Step 4: Patch the binary to jump
    print("Step 4")
    patch_addr = jump_il.address
    view = function.view

    patch_value = view.arch.assemble(f'jmp 0x{next_jump_addr:x}', patch_addr)

    if (ret_addr - patch_addr) < len(patch_value):
        print("Not enough space", hex(patch_addr), len(patch_value))
        return

    view.write(
        patch_addr,
        patch_value
    )

    return next_jump_addr

def unjmp(first_jump : MediumLevelILInstruction):
    global bb_cache
    bb_cache = {}

    first_jump
    function = first_jump.function.source_function
    view = function.view
    mmlil = first_jump.function

    # step 2: get our mlil basic block
    print("Step 2")
    for bb in mmlil.basic_blocks:
        if bb.start <= first_jump.instr_index < bb.end:
            first_jump_bb = bb
            break
    else:
        return
    
    # step 3: look for all the returns
    print("Step 3")
    returns = []

    for idx in range(first_jump.instr_index+1, len(mmlil)):
        current_il = mmlil[idx]
        if (current_il.operation in 
                (MediumLevelILOperation.MLIL_RET,
                 MediumLevelILOperation.MLIL_RET_HINT,
                 MediumLevelILOperation.MLIL_UNDEF)):
            returns.append(current_il)
        idx += 1
    
    # step 4: find the unconditional jump
    # TODO: switch not_unconditional to a set and do the difference
    print("Step 4")
    unconditional_target = None
    not_unconditional = []
    
    for ret in returns:
        if ret.branch_dependence:
            not_unconditional.append(ret)
        else:
            unconditional_target = ret

    if unconditional_target is None:
        return

    # get the basic block for the unconditional ret
    bb = get_mmlil_bb(mmlil, unconditional_target.instr_index)
    
    # make sure first jump dominates
    print("Step 5")
    if first_jump_bb not in bb.dominators:
        return

    # find the ret that is dependent on first jump and another jump
    # and both need to have the same type of branch
    print("Step 6")
    for ret in not_unconditional:
        dependence = ret.branch_dependence

        # same type of branch
        if len({branch for branch in dependence.values()}) != 1:
            continue

        # exactly two branches
        if len(dependence) != 2:
            continue
        
        # first jump is one of the branches
        if first_jump.instr_index not in dependence:
            continue

        bb = get_mmlil_bb(mmlil, ret.instr_index)
        break
    else:
        return

    print("Step 6")
    second_jump = next(mmlil[i] for i in dependence if i != first_jump.instr_index)

    if second_jump is None:
        return

    print("Step 7")
    if first_jump.condition.operation == MediumLevelILOperation.MLIL_VAR:
        # This could be an if (flag:o) and an if (!(flag:o))
        if second_jump.condition.operation != MediumLevelILOperation.MLIL_NOT:
            first_jump_condition = mmlil[
                mmlil.get_ssa_var_definition(first_jump.ssa_form.condition.src)
            ].src
        else:
            first_jump_condition = first_jump.condition
    else:
        first_jump_condition = first_jump.condition

    if second_jump.condition.operation == MediumLevelILOperation.MLIL_VAR:
        if first_jump.condition.operation != MediumLevelILOperation.MLIL_NOT:
            second_jump_condition = mmlil[
                mmlil.get_ssa_var_definition(second_jump.ssa_form.condition.src)
            ].src
        else:
            second_jump_condition = second_jump.condition
    else:
        second_jump_condition = second_jump.condition

    # make sure the comparisons are opposites
    if cmp_pairs[first_jump_condition.operation] != second_jump_condition.operation:
        return
    
    # make sure the operands are the same
    print("Step 8")
    first_ops = ConditionVisitor().visit(first_jump_condition)
    second_ops = ConditionVisitor().visit(second_jump_condition)

    if isinstance(first_ops, Variable):
        if first_ops != second_ops:
            return
    elif (first_ops[0] not in second_ops or
            first_ops[1] not in second_ops):
        return

    # we have found our two jumps and the unconditional!

    branch_type = next(iter(dependence.values()))

    if branch_type == ILBranchDependence.FalseBranchDependent:
        target = mmlil[first_jump.true].address
        print(f'Jumping to {target:x}')
    else:
        target = mmlil[first_jump.false].address
        print(f'Jumping to {target:x}')

    patch_addr = first_jump.address

    patch_bb = next(bb for bb in function if bb.start <= patch_addr < bb.end)

    patch_value = view.arch.always_branch(
        view.read(
            patch_addr,
            view.get_instruction_length(patch_addr)
        ),
        patch_addr
    )

    if (patch_bb.end - patch_addr) < len(patch_value):
        print("not enough space", repr(patch_value))
        return

    view.write(
        patch_addr,
        patch_value
    )

    return target

def get_mmlil_bb(mmlil: MediumLevelILFunction, idx: int):
    if idx not in bb_cache:
        bb_cache[idx] = next(bb for bb in mmlil.basic_blocks
            if bb.start <= idx < bb.end)
    return bb_cache[idx]

def run(func, il : MediumLevelILInstruction):
    source_function = il.function.source_function
    view = source_function.view
    view.begin_undo_actions()
    target = func(il)
    view.commit_undo_actions()
    target_queue = source_function.session_data.get('next', list())
    target_queue.append(target)
    source_function.session_data['next'] = target_queue
